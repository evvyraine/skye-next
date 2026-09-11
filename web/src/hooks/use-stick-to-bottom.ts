import { useCallback, useEffect, useRef, useState } from "react"

/**
 * Keeps a scroll container pinned to the bottom while new content streams in,
 * unless the reader has scrolled up to read history.
 */
export function useStickToBottom<T extends HTMLElement>(deps: unknown[] = []) {
  const ref = useRef<T | null>(null)
  const stick = useRef(true)
  const [atBottom, setAtBottom] = useState(true)

  const onScroll = useCallback(() => {
    const el = ref.current
    if (!el) return
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 96
    stick.current = near
    setAtBottom(near)
  }, [])

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "smooth") => {
    const el = ref.current
    if (!el) return
    stick.current = true
    setAtBottom(true)
    el.scrollTo({ top: el.scrollHeight, behavior })
  }, [])

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 96
    if (stick.current || near) {
      el.scrollTop = el.scrollHeight
    }
    setAtBottom(near)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  return { ref, atBottom, onScroll, scrollToBottom }
}
