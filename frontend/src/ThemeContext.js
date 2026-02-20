import React, { createContext, useState, useEffect, useContext } from 'react';

export const accentColors = {
  purple: {
    name: 'Purple',
    primary: '#8B5CF6',
    secondary: '#A78BFA',
    gradient: 'linear-gradient(135deg, #8B5CF6 0%, #6D28D9 100%)',
  },
  blue: {
    name: 'Blue',
    primary: '#3B82F6',
    secondary: '#60A5FA',
    gradient: 'linear-gradient(135deg, #3B82F6 0%, #1E40AF 100%)',
  },
  cyan: {
    name: 'Cyan',
    primary: '#06B6D4',
    secondary: '#22D3EE',
    gradient: 'linear-gradient(135deg, #06B6D4 0%, #0891B2 100%)',
  },
  emerald: {
    name: 'Emerald',
    primary: '#10B981',
    secondary: '#34D399',
    gradient: 'linear-gradient(135deg, #10B981 0%, #059669 100%)',
  },
  rose: {
    name: 'Rose',
    primary: '#F43F5E',
    secondary: '#FB7185',
    gradient: 'linear-gradient(135deg, #F43F5E 0%, #E11D48 100%)',
  },
  amber: {
    name: 'Amber',
    primary: '#F59E0B',
    secondary: '#FBB020',
    gradient: 'linear-gradient(135deg, #F59E0B 0%, #D97706 100%)',
  },
};

const ThemeContext = createContext();

export const ThemeProvider = ({ children }) => {
  const [accentColor, setAccentColor] = useState(() => {
    const saved = localStorage.getItem('accentColor');
    return saved || 'purple';
  });

  useEffect(() => {
    const color = accentColors[accentColor];
    document.documentElement.style.setProperty('--accent-primary', color.primary);
    document.documentElement.style.setProperty('--accent-secondary', color.secondary);
    document.documentElement.style.setProperty('--accent-gradient', color.gradient);
    localStorage.setItem('accentColor', accentColor);
  }, [accentColor]);

  return (
    <ThemeContext.Provider value={{ accentColor, setAccentColor, accentColors }}>
      {children}
    </ThemeContext.Provider>
  );
};

export const useTheme = () => {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error('useTheme must be used within ThemeProvider');
  }
  return context;
};
