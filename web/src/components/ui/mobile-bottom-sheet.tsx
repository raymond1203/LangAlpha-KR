import React, { useRef, useEffect } from 'react';
import {
  motion,
  animate,
  AnimatePresence,
  useDragControls,
  useMotionValue,
  type PanInfo,
} from 'framer-motion';

interface MobileBottomSheetProps {
  open: boolean;
  onClose: () => void;
  /** 'fixed' uses height + flex layout (content fills via flex-1). Default 'auto' uses maxHeight. */
  sizing?: 'auto' | 'fixed';
  height?: string;
  className?: string;
  style?: React.CSSProperties;
  children: React.ReactNode;
}

function MobileBottomSheet({
  open,
  onClose,
  sizing = 'auto',
  height = '80vh',
  className,
  style,
  children,
}: MobileBottomSheetProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const dragControls = useDragControls();
  const dragY = useMotionValue(0);

  const handleDragEnd = (_: unknown, info: PanInfo) => {
    if (info.velocity.y > 300 || info.offset.y > 120) {
      onClose();
    }
  };

  // Start drag from the handle — always works
  const handleHandlePointerDown = (e: React.PointerEvent) => {
    dragControls.start(e);
  };

  // Stable ref for onClose so the effect doesn't re-attach on every render
  const onCloseRef = useRef(onClose);
  useEffect(() => { onCloseRef.current = onClose; });

  // Unified touch handler: JS manages both scrolling and drag-to-close.
  // touch-action: none on the content div means the browser never takes over
  // the gesture, so we can seamlessly transition from scroll → sheet drag
  // when the user reaches the top and keeps pulling down.
  // Supports nested scrollable children: scrolls the innermost scroller first,
  // then bubbles overflow to the outer container before transitioning to drag.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !open) return;

    let startY = 0;
    let lastY = 0;
    let lastTime = 0;
    let velocityY = 0;
    let mode: 'idle' | 'scroll' | 'drag' = 'idle';
    let momentumRaf = 0;
    let nestedScroller: HTMLElement | null = null;

    // Find nearest scrollable ancestor of touch target (up to but not including root)
    const findNestedScroller = (target: EventTarget | null): HTMLElement | null => {
      let node = target as HTMLElement | null;
      while (node && node !== el) {
        if (node.scrollHeight > node.clientHeight + 1) {
          const ov = getComputedStyle(node).overflowY;
          if (ov === 'auto' || ov === 'scroll') return node;
        }
        node = node.parentElement;
      }
      return null;
    };

    // Scroll nested first, bubble remaining delta to outer container
    // delta > 0 = scrollTop increases (content moves up)
    const applyScroll = (delta: number) => {
      if (nestedScroller) {
        const maxInner = nestedScroller.scrollHeight - nestedScroller.clientHeight;
        const prevInner = nestedScroller.scrollTop;
        nestedScroller.scrollTop = Math.min(maxInner, Math.max(0, prevInner + delta));
        const remaining = delta - (nestedScroller.scrollTop - prevInner);
        if (Math.abs(remaining) > 0.5) {
          el.scrollTop = Math.max(0, el.scrollTop + remaining);
        }
      } else {
        el.scrollTop = Math.max(0, el.scrollTop + delta);
      }
    };

    const allAtTop = (): boolean =>
      el.scrollTop <= 0 && (!nestedScroller || nestedScroller.scrollTop <= 0);

    const onTouchStart = (e: TouchEvent) => {
      cancelAnimationFrame(momentumRaf);
      const y = e.touches[0].clientY;
      startY = y;
      lastY = y;
      lastTime = performance.now();
      velocityY = 0;
      mode = 'idle';
      nestedScroller = findNestedScroller(e.target);
    };

    const onTouchMove = (e: TouchEvent) => {
      e.preventDefault();

      const y = e.touches[0].clientY;
      const now = performance.now();
      const dt = now - lastTime;
      if (dt > 0) velocityY = ((y - lastY) / dt) * 1000; // px/s, positive = finger moving down
      const dy = y - lastY;
      lastY = y;
      lastTime = now;

      // Determine intent from initial movement
      if (mode === 'idle') {
        const totalDelta = y - startY;
        if (Math.abs(totalDelta) < 5) return;
        if (allAtTop() && totalDelta > 0) {
          mode = 'drag';
          startY = y;
        } else {
          mode = 'scroll';
        }
      }

      if (mode === 'drag') {
        const pull = y - startY;
        if (pull < 0) {
          // Reversed — switch to scroll
          dragY.set(0);
          mode = 'scroll';
          applyScroll(-dy);
        } else {
          dragY.set(pull);
        }
      } else if (mode === 'scroll') {
        applyScroll(-dy);
        // Transition to drag when everything is at the top and still pulling down
        if (allAtTop() && dy > 0) {
          mode = 'drag';
          startY = y;
        }
      }
    };

    const onTouchEnd = () => {
      if (mode === 'drag') {
        const dy = dragY.get();
        if (velocityY > 300 || dy > 120) {
          onCloseRef.current();
        } else {
          animate(dragY, 0, { type: 'spring', damping: 28, stiffness: 280 });
        }
      } else if (mode === 'scroll') {
        // Momentum coast
        let v = -velocityY; // positive = scrollTop increasing (content moves up)
        const decel = 0.96;

        const coast = () => {
          v *= decel;
          if (Math.abs(v) < 30) return;
          const prevOuter = el.scrollTop;
          const prevInner = nestedScroller?.scrollTop ?? 0;
          applyScroll(v / 60);
          // Stop when nothing moved (hit bounds)
          if (el.scrollTop === prevOuter && (nestedScroller?.scrollTop ?? 0) === prevInner) return;
          momentumRaf = requestAnimationFrame(coast);
        };

        if (Math.abs(v) > 80) {
          momentumRaf = requestAnimationFrame(coast);
        }
      }
      mode = 'idle';
    };

    el.addEventListener('touchstart', onTouchStart, { passive: true });
    el.addEventListener('touchmove', onTouchMove, { passive: false });
    el.addEventListener('touchend', onTouchEnd, { passive: true });
    el.addEventListener('touchcancel', onTouchEnd, { passive: true });

    return () => {
      cancelAnimationFrame(momentumRaf);
      el.removeEventListener('touchstart', onTouchStart);
      el.removeEventListener('touchmove', onTouchMove);
      el.removeEventListener('touchend', onTouchEnd);
      el.removeEventListener('touchcancel', onTouchEnd);
    };
  }, [open, dragY]);

  return (
    <AnimatePresence>
      {open && (
        <>
          {/* Backdrop — opacity follows drag */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="fixed inset-0 z-40"
            style={{ backgroundColor: 'var(--color-bg-overlay)' }}
            onClick={onClose}
          />
          {/* Sheet */}
          <motion.div
            initial={{ y: '100%' }}
            animate={{ y: 0 }}
            exit={{ y: '100%' }}
            transition={{ type: 'spring', damping: 28, stiffness: 280 }}
            drag="y"
            dragListener={false}
            dragControls={dragControls}
            dragConstraints={{ top: 0, bottom: 0 }}
            dragElastic={{ top: 0, bottom: 1 }}
            onDragEnd={handleDragEnd}
            style={{
              y: dragY,
              backgroundColor: 'var(--color-bg-card)',
              borderColor: 'var(--color-border-muted)',
              ...(sizing === 'fixed' ? { height } : { maxHeight: height }),
            }}
            className={`fixed bottom-0 left-0 right-0 z-50 rounded-t-3xl border-t${sizing === 'fixed' ? ' flex flex-col' : ''}`}
          >
            {/* Drag handle */}
            <div
              className="flex justify-center pt-3 pb-2 cursor-grab active:cursor-grabbing"
              onPointerDown={handleHandlePointerDown}
              style={{ touchAction: 'none' }}
            >
              <div
                className="w-10 h-1 rounded-full"
                style={{ backgroundColor: 'var(--color-border-default)' }}
              />
            </div>
            <div
              ref={scrollRef}
              className={`overflow-y-auto overflow-x-hidden px-4 mobile-scroll-contain${sizing === 'fixed' ? ' flex-1' : ''}${className ? ` ${className}` : ''}`}
              style={{
                ...(sizing === 'auto' ? { maxHeight: `calc(${height} - 36px)` } : {}),
                paddingBottom: 14,
                touchAction: 'none',
                ...style,
              }}
            >
              {children}
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}

export { MobileBottomSheet };
export type { MobileBottomSheetProps };
