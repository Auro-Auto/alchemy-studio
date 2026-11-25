import React, { forwardRef, useCallback, useLayoutEffect, useRef } from 'react';
import { animate, cancelAnimation, observeReducedMotion, reduceMotion } from './motion.js';

const DEFAULT_SELECTOR = 'button[aria-pressed="true"], button[aria-selected="true"], button.active, button.selected, a[aria-current="page"], [data-selected="true"]';
const close = (a, b) => Math.abs(a - b) < 0.25;
const sameRect = (a, b) => a && b && close(a.x, b.x) && close(a.y, b.y) && close(a.width, b.width) && close(a.height, b.height);

function localRect(rect, host, hostRect) {
  // Account for a scaled ancestor as well as borders and horizontal scrolling.
  const scaleX = host.offsetWidth ? hostRect.width / host.offsetWidth : 1;
  const scaleY = host.offsetHeight ? hostRect.height / host.offsetHeight : 1;
  return {
    x: (rect.left - hostRect.left) / (scaleX || 1) - host.clientLeft + host.scrollLeft,
    y: (rect.top - hostRect.top) / (scaleY || 1) - host.clientTop + host.scrollTop,
    width: rect.width / (scaleX || 1),
    height: rect.height / (scaleY || 1),
  };
}

/** Adds a shared selection surface without changing the controls or their events. */
const SelectionRail = forwardRef(function SelectionRail({
  as: Component = 'div', className = '', activeKey, selector = DEFAULT_SELECTOR,
  children, ...props
}, forwardedRef) {
  const hostRef = useRef(null);
  const surfaceRef = useRef(null);
  const selectorRef = useRef(selector);
  const measureRef = useRef(null);
  selectorRef.current = selector;

  const setHost = useCallback((node) => {
    hostRef.current = node;
    if (typeof forwardedRef === 'function') forwardedRef(node);
    else if (forwardedRef) forwardedRef.current = node;
  }, [forwardedRef]);

  useLayoutEffect(() => {
    const host = hostRef.current;
    const surface = surfaceRef.current;
    if (!host || !surface) return undefined;
    let targetRect = null;
    let selectedNode = null;
    let frame = 0;
    let disposed = false;
    const observed = new Set();

    const measure = (allowMotion = true) => {
      if (disposed) return;
      let selected;
      try {
        selected = [...host.querySelectorAll(selectorRef.current)]
          .find((node) => node.closest('.selection-rail') === host);
      } catch { selected = null; }
      const bounds = selected?.getBoundingClientRect();
      if (!bounds || bounds.width < 0.5 || bounds.height < 0.5 || !selected.getClientRects().length) {
        cancelAnimation(surface);
        if (!surface.hidden) surface.hidden = true;
        delete host.dataset.selectionReady;
        targetRect = null;
        selectedNode = null;
        return;
      }
      const hostBounds = host.getBoundingClientRect();
      const next = localRect(bounds, host, hostBounds);
      const unchanged = sameRect(targetRect, next);
      const sameSelection = selectedNode === selected;
      selectedNode = selected;
      surface.style.setProperty('--selection-item-radius', getComputedStyle(selected).borderRadius);
      if (unchanged) return;

      // Read the current animated bounds before cancellation, so repeated input
      // continues from what is actually on screen rather than the last target.
      const previous = targetRect && !surface.hidden
        ? localRect(surface.getBoundingClientRect(), host, hostBounds) : null;
      cancelAnimation(surface);
      Object.assign(surface.style, {
        left: `${next.x}px`, top: `${next.y}px`,
        width: `${next.width}px`, height: `${next.height}px`,
      });
      surface.hidden = false;
      host.dataset.selectionReady = 'true';
      targetRect = next;

      // Layout corrections settle immediately; only a selection travels.
      if (!allowMotion || sameSelection || !previous || reduceMotion() || document.visibilityState === 'hidden') return;
      const dx = previous.x - next.x;
      const dy = previous.y - next.y;
      const distance = Math.hypot(dx, dy);
      const horizontal = Math.abs(dx) >= Math.abs(dy);
      const reach = Math.min(distance * 0.012, 1.7);
      const stretch = Math.min(distance / 5000, 0.025);
      const settleX = distance ? -dx / distance * reach : 0;
      const settleY = distance ? -dy / distance * reach : 0;
      const animation = animate(surface, [
        {
          transform: `translate3d(${dx}px, ${dy}px, 0) scale(${previous.width / next.width}, ${previous.height / next.height})`,
          offset: 0, easing: 'cubic-bezier(0.16, 1, 0.3, 1)',
        },
        {
          transform: `translate3d(${settleX}px, ${settleY}px, 0) scale(${horizontal ? 1 + stretch : 1 - stretch / 2}, ${horizontal ? 1 - stretch / 2 : 1 + stretch})`,
          offset: 0.76, easing: 'cubic-bezier(0.3, 0, 0.3, 1)',
        },
        { transform: 'translate3d(0, 0, 0) scale(1, 1)', offset: 1 },
      ], { duration: Math.min(330, 245 + distance * 0.18), easing: 'linear' });
      if (animation) {
        surface.style.willChange = 'transform';
        const release = () => {
          // An interrupted animation must not clear its successor's hint.
          if (!surface.getAnimations().some((item) => item.playState === 'running')) surface.style.willChange = '';
        };
        animation.addEventListener('finish', release, { once: true });
        animation.addEventListener('cancel', release, { once: true });
      }
    };
    measureRef.current = measure;

    const schedule = (allowMotion = false) => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => measure(allowMotion));
    };
    const resize = typeof ResizeObserver !== 'undefined'
      ? new ResizeObserver(() => schedule(false)) : null;
    const observeChildren = () => {
      const childrenNow = new Set([...host.children].filter((node) => node !== surface));
      for (const node of observed) if (!childrenNow.has(node)) { resize?.unobserve(node); observed.delete(node); }
      for (const node of childrenNow) if (!observed.has(node)) { resize?.observe(node); observed.add(node); }
    };
    resize?.observe(host);
    observeChildren();
    const mutations = new MutationObserver((records) => {
      if (records.every((record) => record.target === surface)) return;
      if (records.some((record) => record.type === 'childList')) observeChildren();
      schedule(true);
    });
    mutations.observe(host, {
      subtree: true, childList: true, attributes: true,
      attributeFilter: ['aria-pressed', 'aria-selected', 'aria-current', 'class', 'data-selected', 'hidden'],
    });
    const layoutChanged = () => schedule(false);
    const settle = () => {
      cancelAnimation(surface);
      surface.style.willChange = '';
      measure(false);
    };
    const visibility = () => { if (document.visibilityState === 'hidden') settle(); else schedule(false); };
    const unsubscribe = observeReducedMotion(settle);
    window.addEventListener('resize', layoutChanged, { passive: true });
    host.addEventListener('scroll', layoutChanged, { passive: true });
    document.addEventListener('visibilitychange', visibility);
    document.fonts?.ready.then(() => { if (!disposed) schedule(false); });
    measure(false);

    return () => {
      disposed = true;
      measureRef.current = null;
      cancelAnimationFrame(frame);
      cancelAnimation(surface);
      surface.style.willChange = '';
      resize?.disconnect();
      mutations.disconnect();
      unsubscribe();
      window.removeEventListener('resize', layoutChanged);
      host.removeEventListener('scroll', layoutChanged);
      document.removeEventListener('visibilitychange', visibility);
    };
  }, []);

  // The explicit key also documents which application state owns this surface.
  // Measuring after each commit covers changed children without cloning them.
  useLayoutEffect(() => { measureRef.current?.(true); });

  return <Component {...props} ref={setHost} className={`selection-rail ${className}`.trim()}>
    <span className="selection-rail__indicator" ref={surfaceRef} aria-hidden="true" hidden />
    {children}
  </Component>;
});

export default SelectionRail;
