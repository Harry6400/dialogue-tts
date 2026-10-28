import {StrictMode} from 'react';
import {createRoot} from 'react-dom/client';
import {DesignPreview} from './DesignPreview';
import '../styles/base.css';

// This independent review entry is served only by Vite development. It is not a production input.
if (import.meta.env.DEV) createRoot(document.getElementById('root')!).render(<StrictMode><DesignPreview/></StrictMode>);
