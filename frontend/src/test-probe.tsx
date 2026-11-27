import { useLocation } from 'react-router'

/** Shows the current URL, so a test can check where a page navigated to. */
export function LocationProbe() {
  const { pathname, search } = useLocation()
  return <output data-testid="location">{pathname + search}</output>
}
