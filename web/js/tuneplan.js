/* What a tuning run has finished and what is left, for the stage tracker on the Tuner page. Pure
   functions over the plan the server publishes with the live status (see _plan in tuner.py): an
   ordered list of {id, label, stages, max}, where `stages` are the stage names the trials carry and
   `max` is the most trials the stage will measure (null while that isn't known). */

/* One entry per planned stage with its state:
   done      the run has moved past it
   running   it is the stage in progress (`current` is the stage name the server reports)
   upcoming  not reached yet
   skipped   known to have nothing to run (max 0), or passed without measuring anything
   n is how many trials it has so far; max never falls below n (a stage can run past its estimate). */
export function stageStates(plan, trials, current) {
  const list = plan || [];
  const at = list.findIndex((s) => s.stages.includes(current));
  return list.map((s, i) => {
    const n = (trials || []).filter((t) => s.stages.includes(t.stage)).length;
    const max = s.max == null ? null : Math.max(s.max, n);
    let state;
    if (s.max === 0 && n === 0) state = 'skipped';
    else if (at === i) state = 'running';
    else if (at === -1) state = n ? 'done' : 'upcoming'; // no stage reported yet, or one outside the plan
    else if (i < at) state = n ? 'done' : 'skipped';
    else state = 'upcoming';
    return { id: s.id, label: s.label, state, n, max };
  });
}

/* The short count under a stage's name. */
export function stageCount(s) {
  if (s.state === 'skipped') return 'skipped';
  if (s.state === 'upcoming') return s.max ? `up to ${s.max}` : 'waiting';
  if (s.state === 'running') return s.max ? `${s.n} of ${s.max}` : `${s.n} measured`;
  return `${s.n} measured`;
}

/* How full the stage's bar is, 0 to 1. A stage of unknown size shows only when it is done. */
export function stageFill(s) {
  if (s.state === 'done') return 1;
  if (s.state === 'running' && s.max) return Math.min(1, s.n / s.max);
  return 0;
}

export const STAGE_WORD = { done: 'done', running: 'running', upcoming: 'not started', skipped: 'skipped' };
