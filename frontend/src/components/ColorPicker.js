import React, { useState } from 'react';
import { Box, IconButton, Popover, Tooltip } from '@mui/material';
import PaletteIcon from '@mui/icons-material/Palette';
import CheckIcon from '@mui/icons-material/Check';
import { useTheme } from '../ThemeContext';

function ColorPicker() {
  const { accentColor, setAccentColor, accentColors } = useTheme();
  const [anchorEl, setAnchorEl] = useState(null);

  const handleClick = (event) => {
    setAnchorEl(event.currentTarget);
  };

  const handleClose = () => {
    setAnchorEl(null);
  };

  const handleColorSelect = (colorKey) => {
    setAccentColor(colorKey);
    handleClose();
  };

  const open = Boolean(anchorEl);

  return (
    <>
      <Tooltip title="Change accent color" placement="left">
        <IconButton
          onClick={handleClick}
          sx={{
            width: 36,
            height: 36,
            background: 'var(--accent-gradient)',
            color: 'white',
            '&:hover': {
              background: 'var(--accent-gradient)',
              opacity: 0.9,
            },
          }}
        >
          <PaletteIcon sx={{ fontSize: 18 }} />
        </IconButton>
      </Tooltip>
      <Popover
        open={open}
        anchorEl={anchorEl}
        onClose={handleClose}
        anchorOrigin={{
          vertical: 'bottom',
          horizontal: 'right',
        }}
        transformOrigin={{
          vertical: 'top',
          horizontal: 'right',
        }}
        PaperProps={{
          sx: {
            background: '#1a1a24',
            border: '1px solid rgba(255, 255, 255, 0.1)',
            borderRadius: 2,
            p: 2,
            mt: 1,
          },
        }}
      >
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
          {Object.entries(accentColors).map(([key, color]) => (
            <Box
              key={key}
              onClick={() => handleColorSelect(key)}
              sx={{
                display: 'flex',
                alignItems: 'center',
                gap: 1.5,
                cursor: 'pointer',
                p: 1,
                borderRadius: 1,
                transition: 'all 0.2s',
                '&:hover': {
                  background: 'rgba(255, 255, 255, 0.05)',
                },
              }}
            >
              <Box
                sx={{
                  width: 28,
                  height: 28,
                  borderRadius: 1,
                  background: color.gradient,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  border: accentColor === key ? '2px solid white' : 'none',
                }}
              >
                {accentColor === key && <CheckIcon sx={{ fontSize: 16, color: 'white' }} />}
              </Box>
              <Box sx={{ fontSize: 13, color: 'white', minWidth: 60 }}>{color.name}</Box>
            </Box>
          ))}
        </Box>
      </Popover>
    </>
  );
}

export default ColorPicker;
