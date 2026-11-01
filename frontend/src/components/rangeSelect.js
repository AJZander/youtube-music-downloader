/** Shift-click ranges for lists of checkboxes: tick one, hold Shift and
 *  tick another, and everything between them takes the second one's new
 *  state. Each list keeps its own anchor (the last box clicked). */
import { useCallback, useRef } from 'react'

/** Whether Shift was held — from a checkbox's change event (whose native
 *  event is the click) or a click. */
export const shiftHeld = (e) => !!(e?.nativeEvent?.shiftKey ?? e?.shiftKey)

/** For a checkbox's onMouseDown: stop Shift-click from selecting the page's
 *  text between the two boxes. The click still ticks the box. */
export function keepTextSelection(e) {
  if (e.shiftKey) e.preventDefault()
}

/** Ids between the anchor and `id` in `order`, both ends included, when
 *  Shift is held and both are in the list; otherwise just `id`. */
export function rangeOf(order, anchor, id, event) {
  const from = anchor == null ? -1 : order.indexOf(anchor)
  const to = order.indexOf(id)
  if (!shiftHeld(event) || from < 0 || to < 0) return [id]
  return order.slice(Math.min(from, to), Math.max(from, to) + 1)
}

/** `(order, id, event) => ids` to change: the range from the last id given
 *  when Shift is held, else `[id]`. Remembers `id` as the next anchor.
 *  `order` is the list's ids as shown, leaving out boxes that are disabled. */
export function useShiftRange() {
  const anchor = useRef(null)
  return useCallback((order, id, event) => {
    const ids = rangeOf(order, anchor.current, id, event)
    anchor.current = id
    return ids
  }, [])
}
