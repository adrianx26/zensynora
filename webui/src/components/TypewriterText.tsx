/**
 * TypewriterText — character-level streaming reveal for agent responses.
 *
 * The chat WebSocket delivers text in *chunks* (the LLM provider's natural
 * unit, often a few tokens at a time). Rendering each chunk verbatim
 * causes the UI to jump in bursts — perceptually less smooth than
 * ChatGPT-style character-by-character reveal.
 *
 * This component buffers the incoming string and drains it into the DOM
 * one character at a time at a configurable cadence. When ``active`` is
 * false (streaming finished, or static content), it renders the text
 * directly with no animation overhead.
 *
 * Props:
 *   text:      the *full* current text (the parent keeps appending chunks)
 *   active:    whether the message is still streaming
 *   charsPerSecond: reveal speed; default 60 cps balances smoothness vs.
 *                   keeping up with fast providers without backlog
 *   onCaughtUp: optional callback fired when the displayed text catches
 *               up with the buffered text (useful for autoscroll cues)
 *
 * Implementation notes:
 *   * Uses a single rAF-driven interval rather than one timer per char,
 *     so 1000-char messages don't queue 1000 setTimeouts.
 *   * If ``text`` shrinks (parent reset / new turn), we reset the cursor —
 *     no flashing of stale characters.
 *   * The cursor blink is CSS-driven (no React state churn).
 */

import React, { useEffect, useRef, useState } from 'react';

interface Props {
  text: string;
  active: boolean;
  charsPerSecond?: number;
  onCaughtUp?: () => void;
}

const TypewriterText: React.FC<Props> = ({
  text,
  active,
  charsPerSecond = 60,
  onCaughtUp,
}) => {
  const [visibleLen, setVisibleLen] = useState<number>(active ? 0 : text.length);
  const rafRef = useRef<number | null>(null);
  const lastTickRef = useRef<number>(0);

  // When streaming ends, snap to full text immediately. Avoids a long
  // tail of animation playing out after the model has stopped sending.
  useEffect(() => {
    if (!active) {
      setVisibleLen(text.length);
    }
  }, [active, text.length]);

  // If the parent resets text to a shorter string (new turn), rewind the
  // cursor so we don't display phantom characters from the previous one.
  useEffect(() => {
    if (visibleLen > text.length) {
      setVisibleLen(0);
    }
  }, [text, visibleLen]);

  // The reveal loop. Only runs while streaming AND we're behind the buffer.
  useEffect(() => {
    if (!active) return;
    if (visibleLen >= text.length) return;

    const charsPerMs = charsPerSecond / 1000;

    const tick = (now: number) => {
      if (lastTickRef.current === 0) {
        lastTickRef.current = now;
      }
      const dt = now - lastTickRef.current;
      lastTickRef.current = now;

      // How many chars do we owe by now?
      const advance = Math.max(1, Math.floor(charsPerMs * dt));

      setVisibleLen((prev) => {
        const next = Math.min(prev + advance, text.length);
        // Stop scheduling once we've caught up; the next ``text``
        // change will start the loop again via the effect below.
        if (next < text.length) {
          rafRef.current = requestAnimationFrame(tick);
        } else {
          rafRef.current = null;
          if (onCaughtUp) onCaughtUp();
        }
        return next;
      });
    };

    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      lastTickRef.current = 0;
    };
  }, [active, text, visibleLen, charsPerSecond, onCaughtUp]);

  const displayed = text.slice(0, visibleLen);
  const isCatchingUp = active && visibleLen < text.length;

  return (
    <span className="typewriter-text">
      {displayed}
      {active && (
        <span
          className={'typewriter-cursor' + (isCatchingUp ? ' typing' : '')}
          aria-hidden="true"
        >
          ▋
        </span>
      )}
    </span>
  );
};

export default TypewriterText;
