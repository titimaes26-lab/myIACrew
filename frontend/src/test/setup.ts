import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// Démonte les composants rendus entre deux tests (sans les globals de vitest, RTL ne le fait pas seul).
afterEach(() => cleanup())
