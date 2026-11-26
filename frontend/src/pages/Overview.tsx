import { EmptyState } from '../components/EmptyState'
import { PageHeader } from '../components/PageHeader'

export default function Overview() {
  return (
    <>
      <PageHeader title="Overview" />
      <EmptyState title="Nothing to show yet">
        Vegapunk isn't capturing data from this host yet.
      </EmptyState>
    </>
  )
}
