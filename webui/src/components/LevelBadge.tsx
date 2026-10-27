// Agent level badge (2026-10-27): "L3" next to an agent's name. Levels 1-9
// are only a label for now — they grant no permissions.
import React from 'react';

export const LevelBadge: React.FC<{ level?: number | null }> = ({ level }) => {
  const n = Math.min(9, Math.max(1, Number(level) || 1));
  return <span className="level-badge" title={`Level ${n} of 9 (a label only for now)`}>L{n}</span>;
};
