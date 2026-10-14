import { useCallback, useEffect, useRef, useState } from 'react'
import { Box, Paper } from '@mui/material'

export const STATS_BAR_HEIGHT = 36
const MIN_HEIGHT = 140

/** A pane height that survives a reload, one per pane. */
export function usePaneHeight(storageKey, initial) {
  const [height, setHeight] = useState(() => {
    try { return Number(localStorage.getItem(storageKey)) || initial } catch { return initial }
  })
  const update = useCallback((h) => {
    setHeight(h)
    try { localStorage.setItem(storageKey, String(Math.round(h))) } catch { /* private mode */ }
  }, [storageKey])
  return [height, update]
}

/**
 * Bottom-docked, resizable pane — the way a torrent client shows the selected
 * item. Drag the top edge to resize. The content (header, tabs, body) is the
 * caller's; this only owns the frame and the divider.
 */
export default function BottomPane({ height, onHeight, children }) {
  const resizing = useRef(false)

  useEffect(() => {
    const move = (e) => {
      if (!resizing.current) return
      const y = e.touches ? e.touches[0].clientY : e.clientY
      const next = window.innerHeight - y - STATS_BAR_HEIGHT
      onHeight(Math.max(MIN_HEIGHT, Math.min(next, window.innerHeight * 0.85)))
    }
    const stop = () => {
      if (!resizing.current) return
      resizing.current = false
      document.body.style.userSelect = ''
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('touchmove', move)
    window.addEventListener('mouseup', stop)
    window.addEventListener('touchend', stop)
    return () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('touchmove', move)
      window.removeEventListener('mouseup', stop)
      window.removeEventListener('touchend', stop)
    }
  }, [onHeight])

  return (
    <Paper elevation={8} square sx={{
      position: 'fixed', left: 0, right: 0, bottom: STATS_BAR_HEIGHT, height,
      zIndex: (theme) => theme.zIndex.appBar - 1,
      display: 'flex', flexDirection: 'column',
      borderTop: 1, borderColor: 'divider',
    }}>
      <Box
        onMouseDown={() => { resizing.current = true; document.body.style.userSelect = 'none' }}
        onTouchStart={() => { resizing.current = true }}
        sx={{
          height: 8, flexShrink: 0, cursor: 'ns-resize',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          '&:hover': { bgcolor: 'action.hover' },
        }}>
        <Box sx={{ width: 40, height: 3, borderRadius: 2, bgcolor: 'divider' }} />
      </Box>
      {children}
    </Paper>
  )
}
