import type {
  BossInfo,
  Edits,
  ExportJob,
  Material,
  Project,
  PutEditsResponse,
  PutJunctionBody,
  ScriptModel,
  Timeline,
} from './types'

const API_BASE = '/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (init?.body != null && !(init.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json')
  }
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
  })

  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as {
      message?: string
      detail?: string
    }
    throw new Error(payload.message ?? payload.detail ?? `请求失败（${response.status}）`)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export function createProject(name: string): Promise<Project> {
  return request<Project>('/projects', {
    method: 'POST',
    body: JSON.stringify({ name }),
  })
}

export function getProjects(): Promise<Project[]> {
  return request<Project[]>('/projects')
}

export function getProject(projectId: string): Promise<Project> {
  return request<Project>(`/projects/${encodeURIComponent(projectId)}`)
}

export function patchProject(
  projectId: string,
  bossInfo: Partial<BossInfo>,
): Promise<Project> {
  return request<Project>(`/projects/${encodeURIComponent(projectId)}`, {
    method: 'PATCH',
    body: JSON.stringify(bossInfo),
  })
}

export function generateTemplate(
  projectId: string,
  bossInfo: BossInfo,
): Promise<ScriptModel> {
  return request<ScriptModel>(
    `/projects/${encodeURIComponent(projectId)}/script/template`,
    { method: 'POST', body: JSON.stringify(bossInfo) },
  )
}

export function getScript(projectId: string): Promise<ScriptModel> {
  return request<ScriptModel>(`/projects/${encodeURIComponent(projectId)}/script`)
}

export function putScript(
  projectId: string,
  script: ScriptModel,
): Promise<ScriptModel> {
  return request<ScriptModel>(`/projects/${encodeURIComponent(projectId)}/script`, {
    method: 'PUT',
    body: JSON.stringify(script),
  })
}

export function uploadMaterial(
  projectId: string,
  formData: FormData,
): Promise<Material> {
  return request<Material>(`/projects/${encodeURIComponent(projectId)}/materials`, {
    method: 'POST',
    body: formData,
  })
}

export function listMaterials(projectId: string): Promise<Material[]> {
  return request<Material[]>(`/projects/${encodeURIComponent(projectId)}/materials`)
}

export function deleteMaterial(projectId: string, shotIndex: number): Promise<void> {
  return request<void>(
    `/projects/${encodeURIComponent(projectId)}/materials/${shotIndex}`,
    { method: 'DELETE' },
  )
}

export function getThumbnailUrl(projectId: string, shotIndex: number): string {
  return `${API_BASE}/projects/${encodeURIComponent(projectId)}/materials/${shotIndex}/thumbnail`
}

export function getEdits(projectId: string): Promise<Edits> {
  return request<Edits>(`/projects/${encodeURIComponent(projectId)}/edits`)
}

export function putEdits(projectId: string, edits: Edits): Promise<PutEditsResponse> {
  return request<PutEditsResponse>(`/projects/${encodeURIComponent(projectId)}/edits`, {
    method: 'PUT',
    body: JSON.stringify(edits),
  })
}

export function getTimeline(projectId: string): Promise<Timeline> {
  return request<Timeline>(`/projects/${encodeURIComponent(projectId)}/timeline`)
}

export function putJunction(
  projectId: string,
  junctionIndex: number,
  body: PutJunctionBody,
): Promise<PutEditsResponse> {
  return request<PutEditsResponse>(
    `/projects/${encodeURIComponent(projectId)}/junctions/${junctionIndex}`,
    { method: 'PUT', body: JSON.stringify(body) },
  )
}

export function previewJunctionUrl(
  projectId: string,
  junctionIndex: number,
  before = 1.5,
  after = 1.5,
  width = 360,
): string {
  const query = new URLSearchParams({
    before: String(before),
    after: String(after),
    w: String(width),
  })
  return `${API_BASE}/projects/${encodeURIComponent(projectId)}/preview/junction/${junctionIndex}?${query}`
}

export function startExport(projectId: string): Promise<{ job_id: string }> {
  return request<{ job_id: string }>(
    `/projects/${encodeURIComponent(projectId)}/render/export`,
    { method: 'POST' },
  )
}

export function getJob(jobId: string): Promise<ExportJob> {
  return request<ExportJob>(`/jobs/${encodeURIComponent(jobId)}`)
}

export function exportUrl(projectId: string): string {
  return `${API_BASE}/projects/${encodeURIComponent(projectId)}/exports/final.mp4`
}
