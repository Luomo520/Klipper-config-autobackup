import axios from 'axios'

export type CloudBackupState = 'queued' | 'archiving' | 'uploading' | 'success' | 'failed'
export type CloudBackupProvider = 'baidu' | 'github'

export interface CloudBackupJob {
  job_id: string;
  state: CloudBackupState;
  progress: number;
  stage: string;
  reason: string;
  roots: string[];
  created_at: number;
  finished_at?: number;
  archive_name?: string;
  archive_size?: number;
  uploaded_bytes?: number;
  upload_total_bytes?: number;
  upload_progress?: number;
  current_file?: string;
  uploaded_files?: number;
  upload_total_files?: number;
  sha256?: string;
  remote_path?: string;
  remote_manifest?: string;
  upload_mode?: string;
  download_available?: boolean;
  error?: string;
  trigger?: 'manual' | 'automatic';
  auth_mode?: CloudBackupAuthMode;
  provider: CloudBackupProvider;
}

export interface CloudBackupDownload {
  root: string;
  filename: string;
  size: number;
  sha256: string;
  expires_at: number;
}

export interface CloudBackupOAuth {
  state: 'idle' | 'starting' | 'pending' | 'authorizing' |
    'authorized' | 'expired' | 'error';
  verification_url?: string;
  expires_at?: number;
  message?: string;
}

export type CloudBackupAuthMode = 'bypy' | 'web_password'
export type CloudBackupAutoMode = 'interval' | 'startup'

export interface CloudBackupAutoStatus {
  enabled: boolean;
  mode: CloudBackupAutoMode;
  interval_days: number;
  startup_delay_minutes: number;
  next_run_at?: number | null;
  last_run_at?: number | null;
  last_success_at?: number | null;
  message?: string;
}

export interface CloudBackupWebLogin {
  state: 'idle' | 'starting' | 'signing_in' | 'verification_required' |
    'manual_verification' | 'authorized' | 'expired' |
    'environment_missing' | 'error';
  message?: string;
  challenge_type?: 'captcha' | 'sms' | 'interactive' | 'qr';
  screenshot_data?: string;
}

export interface CloudBackupStatus {
  version: string;
  ready: boolean;
  provider: CloudBackupProvider;
  auth_mode: CloudBackupAuthMode;
  configured: boolean;
  authorized: boolean;
  available_roots: string[];
  oauth: CloudBackupOAuth;
  web_login: CloudBackupWebLogin;
  active_job: CloudBackupJob | null;
  download_in_progress: boolean;
  auto_backup: CloudBackupAutoStatus;
  history_updated_at?: number | null;
}

export interface CloudBackupConfig {
  provider: CloudBackupProvider;
  auth_mode: CloudBackupAuthMode;
  web_username: string;
  has_web_password: boolean;
  has_github_token: boolean;
  credential_source: 'moonraker_secrets' | 'fluidd';
  web_remote_path: string;
  bypy_remote_path: string;
  bypy_app_root: string;
  bypy_executable_available: boolean;
  github_owner: string;
  github_repo: string;
  github_branch: string;
  github_path: string;
  web_browser_configured: boolean;
  retain_local: number;
  auto_backup_enabled: boolean;
  auto_backup_mode: CloudBackupAutoMode;
  auto_backup_interval_days: number;
  auto_backup_startup_delay_minutes: number;
  available_roots: Array<{ name: string }>;
  selected_roots: string[];
}

export interface CloudBackupConfigInput {
  provider: CloudBackupProvider;
  auth_mode: CloudBackupAuthMode;
  web_username?: string;
  web_password?: string;
  github_token?: string;
  web_remote_path: string;
  bypy_remote_path: string;
  github_owner: string;
  github_repo: string;
  github_branch: string;
  github_path: string;
  selected_roots: string[];
  auto_backup_enabled: boolean;
  auto_backup_mode: CloudBackupAutoMode;
  auto_backup_interval_days: number;
  auto_backup_startup_delay_minutes: number;
}

function unwrapMoonrakerResult<T> (payload: any): T {
  return payload?.result != null ? payload.result as T : payload as T
}

export async function getCloudBackupStatus (): Promise<CloudBackupStatus> {
  const response = await axios.get('/server/cloud_backup/status')
  return unwrapMoonrakerResult<CloudBackupStatus>(response.data)
}

export async function getCloudBackupConfig (): Promise<CloudBackupConfig> {
  const response = await axios.get('/server/cloud_backup/config')
  return unwrapMoonrakerResult<CloudBackupConfig>(response.data)
}

export async function saveCloudBackupConfig (
  input: CloudBackupConfigInput
): Promise<CloudBackupConfig> {
  const response = await axios.post('/server/cloud_backup/config', input)
  return unwrapMoonrakerResult<CloudBackupConfig>(response.data)
}

export async function startCloudBackupOAuth (): Promise<CloudBackupOAuth> {
  const response = await axios.post('/server/cloud_backup/oauth/device')
  return unwrapMoonrakerResult<CloudBackupOAuth>(response.data)
}

export async function getCloudBackupOAuthStatus (): Promise<CloudBackupOAuth> {
  const response = await axios.get('/server/cloud_backup/oauth/status')
  return unwrapMoonrakerResult<CloudBackupOAuth>(response.data)
}

export async function submitCloudBackupOAuthCode (
  authorizationCode: string
): Promise<CloudBackupOAuth> {
  const response = await axios.post('/server/cloud_backup/oauth/verify', {
    authorization_code: authorizationCode,
  })
  return unwrapMoonrakerResult<CloudBackupOAuth>(response.data)
}

export async function revokeCloudBackupOAuth (): Promise<void> {
  await axios.post('/server/cloud_backup/oauth/revoke')
}

export async function startCloudBackupWebLogin (): Promise<CloudBackupWebLogin> {
  const response = await axios.post('/server/cloud_backup/web/login')
  return unwrapMoonrakerResult<CloudBackupWebLogin>(response.data)
}

export async function getCloudBackupWebStatus (): Promise<CloudBackupWebLogin> {
  const response = await axios.get('/server/cloud_backup/web/status')
  return unwrapMoonrakerResult<CloudBackupWebLogin>(response.data)
}

export async function submitCloudBackupWebVerification (
  verificationCode: string
): Promise<CloudBackupWebLogin> {
  const response = await axios.post('/server/cloud_backup/web/verify', {
    verification_code: verificationCode,
  })
  return unwrapMoonrakerResult<CloudBackupWebLogin>(response.data)
}

export async function logoutCloudBackupWeb (): Promise<void> {
  await axios.post('/server/cloud_backup/web/logout')
}

export async function logoutCloudBackupGithub (): Promise<void> {
  await axios.post('/server/cloud_backup/github/logout')
}

export async function createCloudBackup (
  reason: string,
  roots: string[]
): Promise<CloudBackupJob> {
  const response = await axios.post('/server/cloud_backup/backup', { reason, roots })
  return unwrapMoonrakerResult<{ job: CloudBackupJob }>(response.data).job
}

export async function getCloudBackupHistory (
  limit: number = 30
): Promise<CloudBackupJob[]> {
  const response = await axios.get('/server/cloud_backup/history', { params: { limit } })
  return unwrapMoonrakerResult<{ jobs: CloudBackupJob[] }>(response.data).jobs
}

export async function prepareCloudBackupDownload (
  jobId: string
): Promise<CloudBackupDownload> {
  const response = await axios.post('/server/cloud_backup/download', {
    job_id: jobId,
  })
  return unwrapMoonrakerResult<CloudBackupDownload>(response.data)
}
