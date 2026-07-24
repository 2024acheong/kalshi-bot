import { formatDateTime, formatNumber } from '@/components/Format'
import { StatusBadge } from '@/components/StatusBadge'
import { supabase } from '@/lib/supabase'

export const dynamic = 'force-dynamic'

type ModelRow = {
  id: string
  name: string | null
  version: string | null
  artifact_path: string | null
  trained_at: string | null
  train_metrics_json: Record<string, unknown> | null
}

function metricNumber(metrics: Record<string, unknown> | null, key: string) {
  const value = metrics?.[key]
  if (typeof value === 'number' || typeof value === 'string') {
    return formatNumber(value)
  }
  return '-'
}

function metricFlag(metrics: Record<string, unknown> | null) {
  return metrics?.synthetic_placeholder === false ? 'real' : 'synthetic'
}

export default async function ModelsPage() {
  const [modelsResponse, macroOutcomesResponse, weatherOutcomesResponse] = await Promise.all([
    supabase
      .from('model_registry')
      .select('id,name,version,artifact_path,trained_at,train_metrics_json')
      .order('trained_at', { ascending: false })
      .limit(100),
    supabase.from('macro_market_outcomes').select('id', { count: 'exact', head: true }),
    supabase.from('weather_market_outcomes').select('id', { count: 'exact', head: true }),
  ])

  const firstError =
    modelsResponse.error ?? macroOutcomesResponse.error ?? weatherOutcomesResponse.error
  if (firstError) {
    return <div className="card red">Error loading models: {firstError.message}</div>
  }

  const models = (modelsResponse.data ?? []) as ModelRow[]
  const latestByName = new Map<string, ModelRow>()
  for (const model of models) {
    if (model.name && !latestByName.has(model.name)) {
      latestByName.set(model.name, model)
    }
  }
  const latest = [...latestByName.values()]
  const realModels = latest.filter((model) => metricFlag(model.train_metrics_json) === 'real').length

  return (
    <>
      <section className="pageHeader">
        <div>
          <h1 className="pageTitle">Models</h1>
          <p className="pageKicker">Latest model versions, metrics, training source, and stored labels.</p>
        </div>
        <span className="badge badgeGray">{models.length} registry rows</span>
      </section>

      <section className="grid summaryGrid">
        <div className="card">
          <div className="cardLabel">Latest Models</div>
          <div className="cardValue">{latest.length}</div>
        </div>
        <div className="card">
          <div className="cardLabel">Real-Trained</div>
          <div className="cardValue">{realModels}</div>
        </div>
        <div className="card">
          <div className="cardLabel">Macro Labels</div>
          <div className="cardValue">{macroOutcomesResponse.count ?? 0}</div>
        </div>
        <div className="card">
          <div className="cardLabel">Weather Labels</div>
          <div className="cardValue">{weatherOutcomesResponse.count ?? 0}</div>
        </div>
      </section>

      <div className="tableWrap">
        <table className="table">
          <thead>
            <tr>
              <th>Model</th>
              <th>Version</th>
              <th>Source</th>
              <th>Rows</th>
              <th>Accuracy</th>
              <th>Log Loss</th>
              <th>Trained</th>
              <th>Artifact</th>
            </tr>
          </thead>
          <tbody>
            {models.map((model) => {
              const source = metricFlag(model.train_metrics_json)
              return (
                <tr key={model.id}>
                  <td className="blue">{model.name ?? '-'}</td>
                  <td>{model.version ?? '-'}</td>
                  <td>
                    <StatusBadge value={source} />
                  </td>
                  <td>{metricNumber(model.train_metrics_json, 'training_rows')}</td>
                  <td>{metricNumber(model.train_metrics_json, 'accuracy')}</td>
                  <td>{metricNumber(model.train_metrics_json, 'log_loss')}</td>
                  <td>{formatDateTime(model.trained_at)}</td>
                  <td className="muted">{model.artifact_path ?? '-'}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </>
  )
}
