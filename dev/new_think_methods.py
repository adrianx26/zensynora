    # -- Sub-method: Route message ------------------------------------------------

    async def _route_message(
        self,
        user_message: str,
        user_id: str,
        _depth: int
    ) -> tuple:
        """Set up request routing, task timer, and guardrails.

        Returns:
            (request_model, mem, history, _full_history_for_bg) or None if early-exit.
        """
        global _LAST_ACTIVE_TIME
        _LAST_ACTIVE_TIME = time.time()

        # Intelligent Routing: Determine model for THIS request only
        request_model = self.model
        if self._router:
            routed_model = self._router.get_routing_decision(user_message, self.model)
            if routed_model:
                request_model = routed_model

        import uuid

        # Generate unique task ID for this request
        self._current_task_id = f"task_{user_id}_{uuid.uuid4().hex[:8]}_{int(time.time())}"

        # Start task timer for tracking timeouts
        await self._task_timer.start_task_timer(
            task_id=self._current_task_id,
            user_question=user_message,
            on_status_update=self._handle_task_status_update,
            steps_total=5  # Approximate: memory, knowledge, LLM, tools, response
        )

        # Agent Pipeline Integration: Loop prevention
        if _depth > 10:
            logger.warning(f"Max delegation depth reached ({_depth}). Preventing potential infinite loop.")
            await self._task_timer.complete_task(self._current_task_id, success=False,
                                                 error_message="Max delegation depth reached")
            return None

        # Medic Agent: Check loop prevention before processing
        try:
            from myclaw.agents.medic_agent import prevent_infinite_loop
            loop_status = prevent_infinite_loop()
            if "limit reached" in loop_status.lower():
                logger.warning("Execution limit reached by loop prevention")
                await self._task_timer.complete_task(self._current_task_id, success=False,
                                                     error_message="Loop prevention limit reached")
                return None
        except Exception:
            pass

        # Check if task has been cancelled due to timeout
        if self._current_task_id and not self._task_timer.is_task_active(self._current_task_id):
            logger.warning(f"Task {self._current_task_id} was cancelled or timed out")
            return None

        mem = await self._get_memory(user_id)
        await mem.add("user", user_message)

        # Update timer with current step
        await self._task_timer.update_step(self._current_task_id, "memory_loading", 1, 5)

        trigger_hook("on_session_start", user_id, self.name)

        history = await mem.get_history()

        # Feature: Context Summarization moved off hot path
        threshold = getattr(self.config.agents, "summarization_threshold", 10)
        _should_summarize_after = len(history) > threshold
        _full_history_for_bg = history.copy() if _should_summarize_after else None

        return request_model, mem, history, _full_history_for_bg

    # -- Sub-method: Build context ------------------------------------------------

    async def _build_context(
        self,
        user_message: str,
        user_id: str,
        mem: Memory,
        history: list,
        request_model: str
    ) -> tuple:
        """Build the message context: knowledge search + system prompt + hooks.

        Returns:
            (messages, had_kb_results, kb_gap_hint)
        """
        # Optimization #4: Proactive skill pre-loading
        task = asyncio.create_task(
            self._skill_preloader.predict_and_preload(history, user_message)
        )
        self._pending_preloads.add(task)
        task.add_done_callback(self._pending_preloads.discard)

        # Search knowledge base for relevant context
        knowledge_context = await self._search_knowledge_context(user_message, user_id)
        had_kb_results = bool(knowledge_context)

        # If KB gap exists and no context, hint the agent about KB creation and log the gap
        kb_gap_hint = ""
        if not had_kb_results and self._kb_gaps.get(user_id):
            last_gap = next(iter(self._kb_gaps[user_id]))
            kb_gap_hint = (
                f"\n\n[Note: The knowledge base has no entries related to '{last_gap[:60]}'. "
                "Consider using write_to_knowledge() to store useful information for future queries.]"
            )

            # Emit structured log entry for knowledge gap (with deduplication)
            if not self._gap_cache.is_duplicate(last_gap, user_id):
                gap_data = {
                    "event": "knowledge_gap_detected",
                    "query": last_gap,
                    "description": "No knowledge base entries found for query",
                    "user_id": user_id,
                    "session_context": "System will preserve context to avoid redundant empty searches in this session",
                    "timestamp": datetime.utcnow().isoformat(),
                    "recommendation": "Use write_to_knowledge() to create a new entry for future queries"
                }
                kb_gap_logger.info(gap_data)

                # Write to researchers JSONL file
                try:
                    GAP_FILE.parent.mkdir(parents=True, exist_ok=True)
                    with open(GAP_FILE, "a", encoding="utf-8") as f:
                        f.write(json.dumps(gap_data) + "\n")
                except Exception as e:
                    logger.error(f"Failed to record gap to file: {e}")

        # Check if task has been cancelled due to timeout
        if self._current_task_id and not self._task_timer.is_task_active(self._current_task_id):
            logger.warning(f"Task {self._current_task_id} was cancelled or timed out")
            return None

        # Update timer - building system prompt
        if self._current_task_id:
            await self._task_timer.update_step(self._current_task_id, "building_prompt", 2, 5)

        # Build system prompt with knowledge context (async load to avoid blocking)
        system_prompt = await self._load_system_prompt()
        system_content = system_prompt
        if knowledge_context:
            system_content = f"{system_prompt}\n\n{knowledge_context}"
        if kb_gap_hint:
            system_content = f"{system_content}{kb_gap_hint}"

        messages = [{"role": "system", "content": system_content}] + history

        # Trigger pre_llm_call hooks - allow hooks to modify messages
        hook_results = trigger_hook("pre_llm_call", messages, request_model)
        for result in hook_results:
            if result and isinstance(result, list):
                messages = result  # Use modified messages from hook
                logger.debug("pre_llm_call hook modified messages")

        return messages, had_kb_results, kb_gap_hint

    # -- Sub-method: Execute tools ------------------------------------------------

    async def _execute_tools(
        self,
        tool_calls: list,
        messages: list,
        user_message: str,
        user_id: str,
        mem: Memory,
        _depth: int,
        had_kb_results: bool
    ) -> str:
        """Execute tool calls (parallel + sequential) and return final response.

        Returns:
            Final response string, or error message on failure.
        """
        # Save assistant response with tool_calls to memory (as plain text for now)
        await mem.add("assistant", "")

        # Update timer - executing tools
        if self._current_task_id:
            await self._task_timer.update_step(self._current_task_id, "executing_tools", 4, 5)

        # Collect results per tool_call_id
        tool_results_by_id: Dict[str, str] = {}

        # Determine parallel vs sequential execution
        independent_tools = [tc for tc in tool_calls if is_tool_independent(tc.get("function", {}).get("name", ""))]
        dependent_tools = [tc for tc in tool_calls if not is_tool_independent(tc.get("function", {}).get("name", ""))]

        if len(independent_tools) > 1:
            # Use parallel execution for independent tools
            logger.info(f"Executing {len(independent_tools)} tools in parallel")
            executor = get_parallel_executor()
            exec_results = await executor.execute_tools(independent_tools, user_id)

            # Map results back to tool_call_ids
            for tc, r in zip(independent_tools, exec_results):
                tool_call_id = tc.get("id", "call_default")
                if r["success"]:
                    tool_output = r["result"]
                    if r["tool_name"] == "browse" and self._detect_browse_failure(tool_output):
                        url_match = re.search(r"https?://\S+", tool_output)
                        url = url_match.group(0) if url_match else ""
                        tool_output += self._browse_alternative_hint(url, user_message)
                    content = f"Tool {r["tool_name"]} returned: {tool_output}"
                else:
                    content = f"Tool {r["tool_name"]} error: {r["error"]}"
                tool_results_by_id[tool_call_id] = content
                await mem.add("tool", content)
                # KB extraction for substantial parallel tool results (fire-and-forget)
                if r["success"] and self._kb_auto_extract and self._should_save_tool_result(
                    r["tool_name"], r.get("result", "")
                ):
                    _t = asyncio.create_task(
                        self._save_tool_result_to_kb(
                            r["tool_name"],
                            tc.get("function", {}).get("arguments", {}),
                            r["result"],
                            user_message,
                            user_id,
                        )
                    )
                    self._pending_preloads.add(_t)
                    _t.add_done_callback(self._pending_preloads.discard)
        elif independent_tools:
            # Single independent tool - execute sequentially below along with dependent ones
            dependent_tools = tool_calls

        # Execute dependent (and single independent) tools sequentially
        for tc in dependent_tools:
            tool_name = tc.get("function", {}).get("name", "")
            args = tc.get("function", {}).get("arguments", {})
            tool_call_id = tc.get("id", "call_default")

            if tool_name not in TOOLS:
                content = f"Unknown tool: {tool_name}"
                tool_results_by_id[tool_call_id] = content
                await mem.add("tool", content)
                logger.warning(f"Unknown tool called: {tool_name}")
                continue

            if tool_name == "delegate":
                args["_depth"] = _depth + 1

            start_time = time.time()
            logger.info(f"[AUDIT] Tool execution started: {tool_name} with args: {args}")

            try:
                func = TOOLS[tool_name]["func"]
                if inspect.iscoroutinefunction(func):
                    result = await func(**args)
                else:
                    result = await asyncio.to_thread(func, **args)

                tool_output = str(result)

                # Error Handling Enhancement 1: browse failure -> suggest alternatives
                if tool_name == "browse" and self._detect_browse_failure(tool_output):
                    url = args.get("url", "")
                    tool_output += self._browse_alternative_hint(url, user_message)
                    logger.info(f"Browse failure detected for {url}; alternative hint appended.")

                # Error Handling Enhancement 2: empty KB search -> nudge KB creation
                if tool_name == "search_knowledge":
                    if "No results found" in tool_output or "Error" in tool_output:
                        query = args.get("query", user_message[:60])
                        self._record_kb_gap(user_id, query)
                        tool_output += (
                            f"\n\n[Tip: No knowledge base entries matched '{query}'. "
                            "Use write_to_knowledge() to persist useful information for future use.]"
                        )

                content = f"Tool {tool_name} returned: {tool_output}"
                tool_results_by_id[tool_call_id] = content
                await mem.add("tool", content)
                duration = time.time() - start_time
                logger.info(f"[AUDIT] Tool executed successfully: {tool_name} (took {duration:.2f}s)")
                # KB extraction for substantial sequential tool results (fire-and-forget)
                if self._kb_auto_extract and self._should_save_tool_result(tool_name, tool_output):
                    _t = asyncio.create_task(
                        self._save_tool_result_to_kb(
                            tool_name, args, tool_output, user_message, user_id
                        )
                    )
                    self._pending_preloads.add(_t)
                    _t.add_done_callback(self._pending_preloads.discard)
            except Exception as e:
                logger.error(f"Tool execution error ({tool_name}): {e}")
                logger.error(f"[AUDIT] Tool execution failed: {tool_name} - {e}")
                content = f"Tool error: {e}"
                tool_results_by_id[tool_call_id] = content
                await mem.add("tool", content)

        # Build proper followup messages with assistant + individual tool messages for OpenAI compatibility
        openai_tool_calls = []
        for tc in tool_calls:
            openai_tool_calls.append({
                "id": tc.get("id", "call_default"),
                "type": tc.get("type", "function"),
                "function": {
                    "name": tc["function"]["name"],
                    "arguments": tc["function"].get("arguments_str", "")
                }
            })

        followup = messages + [
            {"role": "assistant", "content": "", "tool_calls": openai_tool_calls}
        ]

        # Append one tool message per tool_call_id in the same order as tool_calls
        for tc in tool_calls:
            tool_call_id = tc.get("id", "call_default")
            content = tool_results_by_id.get(tool_call_id, "Tool was not executed.")
            followup.append({"role": "tool", "tool_call_id": tool_call_id, "content": content})

        # Trigger pre_llm_call hooks for followup
        hook_results = trigger_hook("pre_llm_call", followup, self.model)
        for result in hook_results:
            if result and isinstance(result, list):
                followup = result

        try:
            import httpx as _httpx  # local alias to avoid shadowing outer scope
            final_response, _ = await self.provider.chat(followup, self.model)

            # Trigger post_llm_call hooks for followup
            trigger_hook("post_llm_call", final_response, None)

            # Empty-response recovery after tool-use followup
            if self._is_empty_response(final_response):
                final_response = await self._recover_empty_response(
                    followup, user_message, user_id, had_kb_results
                )

            await mem.add("assistant", final_response)
            return final_response
        except Exception as e:
            logger.error(f"LLM second call error: {e}")
            return f"Tool executed but error getting response: {e}"

    # -- Sub-method: Handle summarization & cleanup -------------------------------

    async def _handle_summarization(
        self,
        user_message: str,
        response: str,
        user_id: str,
        mem: Memory,
        _full_history_for_bg: Optional[list]
    ) -> None:
        """Background KB extraction, session end hooks, and context summarization.

        This is called after the response has been sent to the user.
        All operations are fire-and-forget and never block the response.
        """
        # Background KB auto-extraction (fire-and-forget, never blocks response)
        if self._kb_auto_extract and self._should_extract_knowledge(user_message, response):
            _kb_task = asyncio.create_task(
                self._extract_and_save_knowledge(user_message, response, user_id)
            )
            self._pending_preloads.add(_kb_task)
            _kb_task.add_done_callback(self._pending_preloads.discard)

        # Trigger on_session_end hook
        message_count = len(await mem.get_history()) if hasattr(mem, "get_history") else 0
        trigger_hook("on_session_end", user_id, self.name, message_count)

        # Background context summarization (fire-and-forget, off hot path)
        if _full_history_for_bg:
            _summarize_task = asyncio.create_task(
                self._background_summarize_context(_full_history_for_bg, user_id, mem)
            )
            self._pending_preloads.add(_summarize_task)
            _summarize_task.add_done_callback(self._pending_preloads.discard)

        # Complete task timer successfully
        if self._current_task_id:
            await self._task_timer.complete_task(self._current_task_id, success=True)
            self._current_task_id = None

    # -- Main think() orchestrator ------------------------------------------------

    async def think(self, user_message: str, user_id: str = "default", _depth: int = 0) -> str:
        """Process a user message and return the agent's response.

        Orchestrates the pipeline via sub-methods:
            1. _route_message()    -- routing, timer, guardrails
            2. _build_context()    -- knowledge search, system prompt
            3. LLM call            -- primary reasoning
            4. _execute_tools()    -- tool execution (if any)
            5. _handle_summarization() -- background cleanup
        """
        # 1. Route message
        route_result = await self._route_message(user_message, user_id, _depth)
        if route_result is None:
            return "Sorry, this task took too long to complete and has been cancelled. Please try again with a simpler request."
        request_model, mem, history, _full_history_for_bg = route_result

        # 2. Build context
        context_result = await self._build_context(user_message, user_id, mem, history, request_model)
        if context_result is None:
            return "Sorry, this task took too long to complete and has been cancelled. Please try again with a simpler request."
        messages, had_kb_results, kb_gap_hint = context_result

        # 3. LLM call
        if self._current_task_id:
            await self._task_timer.update_step(self._current_task_id, "llm_call", 3, 5)

        try:
            import httpx
            response, tool_calls = await self.provider.chat(messages, request_model)
        except httpx.TimeoutException as e:
            logger.error(f"LLM provider timeout: {e}")
            error_msg = "Sorry, the LLM service timed out. Please try again."
            if self._current_task_id:
                await self._task_timer.complete_task(self._current_task_id, success=False, error_message=error_msg)
                self._current_task_id = None
            return error_msg
        except (httpx.ConnectError, ConnectionError) as e:
            logger.error(f"LLM provider connection error: {e}")
            error_msg = "Sorry, I cannot connect to the LLM service. Please check your connection."
            if self._current_task_id:
                await self._task_timer.complete_task(self._current_task_id, success=False, error_message=error_msg)
                self._current_task_id = None
            return error_msg
        except httpx.HTTPStatusError as e:
            logger.error(f"LLM provider HTTP error: {e}")
            error_msg = f"Sorry, the LLM service returned an error: {e.response.status_code}"
            if self._current_task_id:
                await self._task_timer.complete_task(self._current_task_id, success=False, error_message=error_msg)
                self._current_task_id = None
            return error_msg
        except Exception as e:
            logger.exception(f"Unexpected LLM provider error: {e}")
            error_msg = f"Sorry, an unexpected error occurred: {e}"
            if self._current_task_id:
                await self._task_timer.complete_task(self._current_task_id, success=False, error_message=error_msg)
                self._current_task_id = None
            return error_msg

        # Trigger post_llm_call hooks
        hook_results = trigger_hook("post_llm_call", response, tool_calls)
        for result in hook_results:
            if result and isinstance(result, tuple) and len(result) == 2:
                response, tool_calls = result

        # 4. Execute tools (if any) or handle direct response
        if tool_calls:
            final_response = await self._execute_tools(
                tool_calls, messages, user_message, user_id, mem, _depth, had_kb_results
            )
            if final_response.startswith("Tool executed but error"):
                return final_response
            # 5. Handle summarization & cleanup
            await self._handle_summarization(user_message, final_response, user_id, mem, _full_history_for_bg)
            return final_response

        # No tool calls: validate response is non-empty
        if self._is_empty_response(response):
            response = await self._recover_empty_response(
                messages, user_message, user_id, had_kb_results
            )

        await mem.add("assistant", response)

        # 5. Handle summarization & cleanup
        await self._handle_summarization(user_message, response, user_id, mem, _full_history_for_bg)
        return response
