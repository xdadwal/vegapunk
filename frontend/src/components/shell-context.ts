import { createContext, useContext } from 'react'

type Shell = { close: () => void }

export const ShellContext = createContext<Shell | null>(null)

/** Lets navigation items close the mobile drawer. Null outside an AppShell. */
export function useShell(): Shell | null {
  return useContext(ShellContext)
}
