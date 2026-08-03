import type { BossInfo, Project, ScriptModel } from './types'

const API_BASE = '/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
  })

  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as {
      message?: string
      detail?: string
    }
    throw new Error(payload.message ?? payload.detail ?? `请求失败（${response.status}）`)
  }
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
