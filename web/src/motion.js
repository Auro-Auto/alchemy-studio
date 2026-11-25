const running = new WeakMap();
const reducedMotionQuery = '(prefers-reduced-motion: reduce)';

function motionPreference() {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia(reducedMotionQuery)
    : null;
}

/** Read the current preference instead of caching the initial browser setting. */
export function reduceMotion() {
  return motionPreference()?.matches ?? false;
}

/** Subscribe to preference changes; returns an idempotent cleanup function. */
export function observeReducedMotion(callback) {
  const query = motionPreference();
  if (!query) return () => {};
  const change = (event) => callback(event.matches);
  if (query.addEventListener) query.addEventListener('change', change);
  else query.addListener(change);
  return () => {
    if (query.removeEventListener) query.removeEventListener('change', change);
    else query.removeListener(change);
  };
}

export function cancelAnimation(element) {
  const entry = element && running.get(element);
  if (!entry) return;
  entry.cleanup();
  entry.animation.cancel();
}

/**
 * One interruptible WAAPI animation per element. Set its resting styles before
 * calling: finishing, reducing motion or hiding the page reveals those styles.
 * Returns Animation, or null when spatial motion should be skipped.
 */
export function animate(element, keyframes, options = {}) {
  cancelAnimation(element);
  if (!element?.animate || reduceMotion() || document.visibilityState === 'hidden') return null;

  const animation = element.animate(keyframes, {
    duration: 280,
    easing: 'cubic-bezier(0.16, 1, 0.3, 1)',
    fill: 'none',
    ...options,
  });
  let cleaned = false;
  let unsubscribe = () => {};
  const settle = () => cancelAnimation(element);
  const visibility = () => {
    if (document.visibilityState === 'hidden') settle();
  };
  const cleanup = () => {
    if (cleaned) return;
    cleaned = true;
    unsubscribe();
    document.removeEventListener('visibilitychange', visibility);
    animation.removeEventListener('finish', cleanup);
    animation.removeEventListener('cancel', cleanup);
    if (running.get(element)?.animation === animation) running.delete(element);
  };
  running.set(element, { animation, cleanup });
  unsubscribe = observeReducedMotion((reduced) => { if (reduced) settle(); });
  document.addEventListener('visibilitychange', visibility);
  animation.addEventListener('finish', cleanup, { once: true });
  animation.addEventListener('cancel', cleanup, { once: true });
  return animation;
}
