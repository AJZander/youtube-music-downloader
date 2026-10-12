import { Box, Checkbox, Typography } from '@mui/material'

import ReleaseRow from './ReleaseRow.jsx'
import { ownedFully } from './shared.js'

export default function ReleaseSection({
  section, selection, expanded, fetching, titles, query, force,
  onToggle, onToggleTrack, onExpand, onSelectMany, onOpenArtist, onDismiss,
}) {
  // Releases already complete in the library are not picked by select-all.
  const ids = section.releases.filter((r) => force || !ownedFully(r)).map((r) => r.browse_id)
  const picked = ids.filter((id) => selection[id]).length
  return (
    <Box sx={{ mb: 2.5 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 0.5 }}>
        <Checkbox size="small" checked={picked > 0 && picked === ids.length}
          indeterminate={picked > 0 && picked < ids.length} disabled={!ids.length}
          onChange={() => onSelectMany(ids, picked < ids.length)}
          inputProps={{ 'aria-label': `Select all ${section.label}` }} />
        <Typography variant="subtitle2" sx={{ textTransform: 'uppercase', letterSpacing: 0.6 }}>
          {section.label}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {section.releases.length}{picked > 0 && ` · ${picked} selected`}
        </Typography>
      </Box>
      {section.releases.map((r) => (
        <ReleaseRow key={r.browse_id} release={r} entry={selection[r.browse_id]}
          expanded={!!expanded[r.browse_id]} fetching={!!fetching[r.browse_id]}
          titles={titles} query={query} force={force} onToggle={onToggle}
          onToggleTrack={onToggleTrack} onExpand={onExpand}
          onOpenArtist={onOpenArtist} onDismiss={onDismiss} />
      ))}
    </Box>
  )
}
