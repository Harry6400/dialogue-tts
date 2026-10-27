import {StrictMode} from 'react';
import {render, screen} from '@testing-library/react';
import {expect, it, vi} from 'vitest';
import {App} from '../app/App';

it('clearly labels preparation and never submits on repeated mounting', () => {
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
  const mounted = render(<StrictMode><App /></StrictMode>);
  expect(screen.getByText('正式页面尚未切换')).toBeInTheDocument();
  expect(screen.getByRole('button', {name: '读取本机配置'})).toBeEnabled();
  expect(fetch).not.toHaveBeenCalled(); mounted.unmount(); vi.unstubAllGlobals();
});
