import '@testing-library/jest-dom/vitest';
import {cleanup} from '@testing-library/react';
import {afterEach} from 'vitest';
afterEach(cleanup);
if (!HTMLDialogElement.prototype.showModal) HTMLDialogElement.prototype.showModal = function () {this.setAttribute('open', '');};
if (!HTMLDialogElement.prototype.close) HTMLDialogElement.prototype.close = function () {if (this.open) {this.removeAttribute('open'); this.dispatchEvent(new Event('close'));}};
if (!HTMLElement.prototype.scrollIntoView) HTMLElement.prototype.scrollIntoView = function () {};
