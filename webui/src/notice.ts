// 2026-11-08: page messages ("Saved.", "Proposal approved." …) stayed on the screen forever.
// useAutoClear(notice, setNotice): a success message goes away by itself within 3 seconds.
// An error stays until the next action replaces or clears it, so it can be read and copied.
import { useEffect, type Dispatch, type SetStateAction } from 'react';

type Notice = { text: string } & ({ kind: 'ok' | 'error' } | { ok: boolean });

export const NOTICE_MS = 2500;  // gone within 3 s, also when the page is busy right after saving

export const isSuccess = (n: Notice): boolean => ('kind' in n ? n.kind === 'ok' : n.ok);

export function useAutoClear<T extends Notice>(notice: T | null, setNotice: Dispatch<SetStateAction<T | null>>) {
  useEffect(() => {
    if (!notice || !isSuccess(notice)) return;
    const timer = setTimeout(() => setNotice(cur => (cur === notice ? null : cur)), NOTICE_MS);
    return () => clearTimeout(timer);
  }, [notice, setNotice]);
}
