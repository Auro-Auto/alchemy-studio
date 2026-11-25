import React, { useCallback, useEffect, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { observeReducedMotion, reduceMotion } from './motion.js';

const referenceNames = new Map();
export function referenceMotionName(id) {
  if (!referenceNames.has(id)) referenceNames.set(id, `alchemy-reference-${referenceNames.size + 1}`);
  return referenceNames.get(id);
}

export function useReferenceSwap(update) {
  const action = useRef(update);
  const active = useRef(null);
  const mounted = useRef(true);
  const queued = useRef(0);
  const [pending, setPending] = useState(false);
  action.current = update;

  useEffect(() => {
    mounted.current = true;
    const stop = () => active.current?.skipTransition();
    const visibility = () => {
      document.documentElement.dataset.motionPaused = String(document.hidden);
      if (document.hidden) stop();
    };
    const unsubscribe = observeReducedMotion((reduced) => { if (reduced) stop(); });
    visibility();
    document.addEventListener('visibilitychange', visibility);
    return () => {
      mounted.current = false;
      stop();
      unsubscribe();
      document.removeEventListener('visibilitychange', visibility);
      document.documentElement.classList.remove('reference-swap');
      delete document.documentElement.dataset.motionPaused;
    };
  }, []);

  const swap = useCallback(() => {
    const images = [...document.querySelectorAll('.ingredients-grid img.reference-image')];
    const names = images.map((image) => image.style.viewTransitionName);
    if (!document.startViewTransition || reduceMotion() || document.hidden
      || images.length !== 2 || new Set(names).size !== 2
      || images.some((image) => !image.complete || !image.naturalWidth)) {
      action.current();
      return;
    }
    active.current?.skipTransition();
    document.documentElement.classList.add('reference-swap');
    queued.current += 1;
    setPending(true);
    const apply = () => {
      queued.current -= 1;
      if (mounted.current) flushSync(() => {
        action.current();
        if (!queued.current) setPending(false);
      });
    };
    try {
      const transition = document.startViewTransition(apply);
      active.current = transition;
      transition.ready.catch(() => {});
      transition.updateCallbackDone.catch(() => {});
      transition.finished.catch(() => {}).finally(() => {
        if (active.current === transition) {
          active.current = null;
          document.documentElement.classList.remove('reference-swap');
        }
      });
    } catch {
      document.documentElement.classList.remove('reference-swap');
      apply();
    }
  }, []);
  return { swap, pending };
}

export function ActivityBars() {
  const element = useRef(null);
  useEffect(() => {
    if (typeof IntersectionObserver === 'undefined') return undefined;
    const observer = new IntersectionObserver(([entry]) => {
      if (element.current) element.current.dataset.visible = String(entry.isIntersecting);
    });
    observer.observe(element.current);
    return () => observer.disconnect();
  }, []);
  return <span ref={element} className="activity-bars" aria-hidden="true"><i /><i /><i /></span>;
}
