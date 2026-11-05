import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { PrefsProvider } from './prefs';
import { ProjectProvider } from './projects';
import { ConfirmProvider } from './components/Confirm';
import './styles.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <PrefsProvider>
      <ConfirmProvider><ProjectProvider><App /></ProjectProvider></ConfirmProvider>
    </PrefsProvider>
  </StrictMode>,
);
