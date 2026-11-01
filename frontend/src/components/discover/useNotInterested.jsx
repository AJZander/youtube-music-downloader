import { useCallback, useState } from 'react'
import { Alert, Button, IconButton, Snackbar } from '@mui/material'
import CloseIcon from '@mui/icons-material/Close'

import { api } from '../../api.js'

/** "Not interested": hide releases from Discover, with a few seconds to
 *  take it back. `onChange` runs after a hide or an undo, to re-read. */
export function useNotInterested({ onChange, onError }) {
  const [undo, setUndo] = useState(null)   // the last releases hidden: {ids, text, key}

  const hide = useCallback(async (ids, text) => {
    await api.discoverDismissReleases(ids)
    setUndo({ ids, text, key: Date.now() })
    onChange?.()
  }, [onChange])

  const restore = useCallback(async (ids) => {
    try {
      await api.discoverRestoreReleases(ids)
      onChange?.()
    } catch (e) {
      onError?.(e.message)
    }
  }, [onChange, onError])

  const snackbar = (
    <Snackbar open={!!undo} key={undo?.key} autoHideDuration={10000}
      anchorOrigin={{ vertical: 'top', horizontal: 'center' }}
      onClose={(_, reason) => { if (reason !== 'clickaway') setUndo(null) }}>
      {undo ? (
        <Alert severity="info" variant="filled" sx={{ width: '100%', alignItems: 'center' }}
          action={(
            <>
              <Button color="inherit" size="small"
                onClick={() => { setUndo(null); restore(undo.ids) }}>
                Undo
              </Button>
              <IconButton color="inherit" size="small" aria-label="Close"
                onClick={() => setUndo(null)}>
                <CloseIcon fontSize="small" />
              </IconButton>
            </>
          )}>
          {undo.text}
        </Alert>
      ) : <span />}
    </Snackbar>
  )

  return { hide, restore, snackbar }
}
