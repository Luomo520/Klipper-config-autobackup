import axios from 'axios'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  createCloudBackup,
  getCloudBackupHistory,
  getCloudBackupStatus,
  logoutCloudBackupGithub,
  prepareCloudBackupDownload,
  saveCloudBackupConfig,
  submitCloudBackupOAuthCode,
  startCloudBackupWebLogin,
  submitCloudBackupWebVerification,
  type CloudBackupConfigInput,
} from '../cloudBackup'

vi.mock('axios', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

const mockedAxios = vi.mocked(axios)

describe('cloud backup API adapter', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('unwraps Moonraker status responses', async () => {
    mockedAxios.get.mockResolvedValueOnce({
      data: {
        result: {
          ready: true,
          version: '0.1alpha',
          provider: 'baidu',
          auth_mode: 'bypy',
          configured: true,
          authorized: false,
          available_roots: ['config'],
          oauth: { state: 'idle' },
          web_login: { state: 'idle' },
          active_job: null,
          download_in_progress: false,
          auto_backup: {
            enabled: true,
            mode: 'interval',
            interval_days: 3,
            startup_delay_minutes: 15,
            next_run_at: 200,
            last_run_at: 100,
            last_success_at: 120,
          },
          history_updated_at: 120,
        },
      },
    })

    const status = await getCloudBackupStatus()

    expect(status.ready).toBe(true)
    expect(status.available_roots).toEqual(['config'])
    expect(status.auto_backup.last_success_at).toBe(120)
    expect(status.history_updated_at).toBe(120)
    expect(mockedAxios.get).toHaveBeenCalledWith('/server/cloud_backup/status')
  })

  it('sends only the explicitly supplied configuration fields', async () => {
    const input: CloudBackupConfigInput = {
      provider: 'baidu',
      auth_mode: 'bypy' as const,
      web_remote_path: '/3D打印机备份',
      bypy_remote_path: '/3D打印机备份',
      github_owner: 'printer-backups',
      github_repo: 'printer-config-backups',
      github_branch: 'main',
      github_path: 'printer-backups',
      selected_roots: ['config'],
      auto_backup_enabled: true,
      auto_backup_mode: 'interval' as const,
      auto_backup_interval_days: 3,
      auto_backup_startup_delay_minutes: 15,
    }
    mockedAxios.post.mockResolvedValueOnce({ data: { result: input } })

    await saveCloudBackupConfig(input)

    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/server/cloud_backup/config',
      input
    )
    expect(mockedAxios.post.mock.calls[0][1]).not.toHaveProperty('web_password')
  })

  it('submits the one-time bypy authorization code', async () => {
    mockedAxios.post.mockResolvedValueOnce({
      data: { result: { state: 'authorizing' } },
    })

    expect(await submitCloudBackupOAuthCode('temporary-code')).toEqual({
      state: 'authorizing',
    })
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/server/cloud_backup/oauth/verify',
      { authorization_code: 'temporary-code' }
    )
  })

  it('uses isolated endpoints for web login and verification', async () => {
    mockedAxios.post
      .mockResolvedValueOnce({ data: { result: { state: 'starting' } } })
      .mockResolvedValueOnce({ data: { result: { state: 'signing_in' } } })

    expect(await startCloudBackupWebLogin()).toEqual({ state: 'starting' })
    expect(await submitCloudBackupWebVerification('123456')).toEqual({
      state: 'signing_in',
    })
    expect(mockedAxios.post).toHaveBeenNthCalledWith(
      1,
      '/server/cloud_backup/web/login'
    )
    expect(mockedAxios.post).toHaveBeenNthCalledWith(
      2,
      '/server/cloud_backup/web/verify',
      { verification_code: '123456' }
    )
  })

  it('uses a separate endpoint to remove the GitHub token', async () => {
    mockedAxios.post.mockResolvedValueOnce({ data: { result: { logged_out: true } } })

    await logoutCloudBackupGithub()

    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/server/cloud_backup/github/logout'
    )
  })

  it('returns the created job and history list', async () => {
    const job = {
      job_id: 'job123',
      state: 'queued' as const,
      progress: 0,
      uploaded_bytes: 0,
      upload_total_bytes: 55763204,
      upload_progress: 0,
      uploaded_files: 0,
      upload_total_files: 108,
      stage: 'queued',
      reason: 'Detailed printer configuration change',
      roots: ['config'],
      created_at: 123,
      provider: 'github' as const,
    }
    mockedAxios.post.mockResolvedValueOnce({ data: { result: { job } } })
    mockedAxios.get.mockResolvedValueOnce({ data: { result: { jobs: [job] } } })

    expect(await createCloudBackup(job.reason, job.roots)).toEqual(job)
    expect(await getCloudBackupHistory()).toEqual([job])
  })

  it('prepares a controlled Moonraker download for a historical job', async () => {
    const prepared = {
      root: 'cloud_backup_downloads',
      filename: '20260726_220000_job123.tar.gz',
      size: 12345,
      sha256: 'a'.repeat(64),
      expires_at: 456,
    }
    mockedAxios.post.mockResolvedValueOnce({ data: { result: prepared } })

    expect(await prepareCloudBackupDownload('abcdef123456')).toEqual(prepared)
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/server/cloud_backup/download',
      { job_id: 'abcdef123456' }
    )
  })
})
