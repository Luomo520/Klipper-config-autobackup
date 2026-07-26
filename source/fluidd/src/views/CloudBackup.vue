<template>
  <v-container
    fluid
    class="cloud-backup pa-0"
  >
    <div class="d-flex flex-wrap align-center mb-4">
      <div>
        <h1 class="text-h5 font-weight-medium mb-1">
          云端配置备份
          <span
            v-if="status?.version"
            class="text-caption text--secondary ml-2"
          >v{{ status.version }}</span>
        </h1>
        <div class="text-body-2 text--secondary">
          当前 Fluidd 主机直接创建配置快照并上传，无需 PC 中转
        </div>
      </div>
      <v-spacer />
      <v-chip
        small
        :color="statusColor"
        :outlined="!isAuthorized"
      >
        <v-icon
          small
          left
        >
          {{ statusIcon }}
        </v-icon>
        {{ statusLabel }}
      </v-chip>
    </div>

    <v-alert
      v-if="!loading && !backendAvailable"
      type="warning"
      text
      prominent
      class="mb-4"
    >
      Moonraker 尚未加载 <code>[cloud_backup]</code> 组件。安装后重启 Moonraker，
      此页面会自动连接，不需要绑定打印机。
    </v-alert>

    <v-alert
      v-else-if="!loading && connectionStale"
      type="warning"
      text
      dense
      class="mb-4"
    >
      与 Moonraker 的云备份连接暂时中断，页面会自动重试。恢复连接前不会允许修改配置。
    </v-alert>

    <v-alert
      v-if="errorMessage"
      type="error"
      text
      dismissible
      class="mb-4"
      @input="errorMessage = ''"
    >
      {{ errorMessage }}
    </v-alert>

    <template v-if="backendAvailable">
      <section class="cloud-section py-4 mb-4">
        <div class="section-heading d-flex align-center mb-4">
          <div class="step-index mr-3">
            1
          </div>
          <div>
            <h2 class="text-subtitle-1 font-weight-medium">
              连接方式与配置
            </h2>
            <div class="text-caption text--secondary">
              选择备份目标并保存对应连接信息
            </div>
          </div>
        </div>

        <v-btn-toggle
          v-model="provider"
          mandatory
          color="primary"
          class="mb-4 provider-toggle"
          :disabled="savingConfig || jobRunning || authorizing"
        >
          <v-btn value="baidu">
            百度网盘
          </v-btn>
          <v-btn value="github">
            GitHub
          </v-btn>
        </v-btn-toggle>

        <template v-if="provider === 'baidu'">
          <div class="auth-method-panel mb-4">
            <div class="text-caption font-weight-medium mb-2">
              登录方式
            </div>
            <v-btn-toggle
              v-model="authMode"
              mandatory
              color="primary"
              class="auth-mode-toggle"
              :disabled="savingConfig || jobRunning || authorizing"
            >
              <v-btn value="bypy">
                命令行授权
              </v-btn>
              <v-btn value="web_password">
                账号密码登录
              </v-btn>
            </v-btn-toggle>
          </div>

          <v-alert
            v-if="authMode === 'web_password'"
            type="warning"
            text
            dense
            class="mb-4"
          >
            密码保存在打印机的权限受限文件中，仅供百度网页登录使用。验证码、短信验证或
            风控出现时需要在此页继续处理。
          </v-alert>

          <v-row v-if="authMode === 'web_password'">
            <v-col
              cols="12"
              md="6"
            >
              <v-text-field
                v-model.trim="webUsername"
                label="百度账号"
                outlined
                dense
                autocomplete="username"
                :disabled="savingConfig || authorizing"
              />
            </v-col>
            <v-col
              cols="12"
              md="6"
            >
              <v-text-field
                v-model="webPassword"
                label="百度密码"
                outlined
                dense
                type="password"
                autocomplete="current-password"
                :placeholder="config?.has_web_password ? '已保存，留空则保持不变' : ''"
                :disabled="savingConfig || authorizing"
              />
            </v-col>
          </v-row>

          <v-alert
            v-else
            type="info"
            text
            dense
            class="mb-4"
          >
            首次授权需要在百度页面确认并粘贴授权码。之后由打印机使用 bypy
            命令行上传，不启动 Chromium。百度限制该方式只能访问“我的应用数据/bypy”。
          </v-alert>

          <v-text-field
            v-model.trim="activeRemotePath"
            :label="authMode === 'web_password' ? '网盘备份目录' : 'bypy 内备份目录'"
            outlined
            dense
            :hint="authMode === 'web_password'
              ? '默认 /3D打印机备份'
              : '实际位置：我的应用数据/bypy/3D打印机备份'"
            persistent-hint
            :disabled="savingConfig || authorizing"
          />
        </template>

        <template v-else>
          <v-alert
            type="warning"
            text
            dense
            class="mb-4"
          >
            建议使用私有仓库和仅含 Contents 读写权限的细粒度令牌。令牌只保存在打印机的
            权限受限凭据文件中，不会返回浏览器。
          </v-alert>
          <v-row>
            <v-col
              cols="12"
              md="6"
            >
              <v-text-field
                v-model.trim="githubOwner"
                label="GitHub 所有者"
                outlined
                dense
                autocomplete="off"
                :disabled="savingConfig"
              />
            </v-col>
            <v-col
              cols="12"
              md="6"
            >
              <v-text-field
                v-model.trim="githubRepo"
                label="仓库名称"
                outlined
                dense
                autocomplete="off"
                :disabled="savingConfig"
              />
            </v-col>
            <v-col
              cols="12"
              md="6"
            >
              <v-text-field
                v-model.trim="githubBranch"
                label="分支"
                outlined
                dense
                :disabled="savingConfig"
              />
            </v-col>
            <v-col
              cols="12"
              md="6"
            >
              <v-text-field
                v-model.trim="githubPath"
                label="仓库内备份目录"
                outlined
                dense
                hint="例如 printer-backups"
                persistent-hint
                :disabled="savingConfig"
              />
            </v-col>
          </v-row>
          <v-text-field
            v-model="githubToken"
            label="Fine-grained personal access token"
            outlined
            dense
            type="password"
            autocomplete="new-password"
            :placeholder="config?.has_github_token ? '已保存，留空则保持不变' : ''"
            :disabled="savingConfig || credentialsFromSecrets"
          />
          <v-alert
            v-if="provider === 'github' && credentialsFromSecrets"
            type="info"
            text
            dense
            class="mb-0"
          >
            GitHub 令牌由 moonraker.secrets 管理。修改后需重启 Moonraker。
          </v-alert>
        </template>

        <app-btn
          color="primary"
          class="mt-4"
          :loading="savingConfig"
          :disabled="!canSaveConfig"
          @click="saveConfig"
        >
          <v-icon
            small
            left
          >
            $save
          </v-icon>
          保存配置
        </app-btn>
      </section>

      <section class="cloud-section py-4 mb-4">
        <div class="section-heading d-flex align-center mb-4">
          <div class="step-index mr-3">
            2
          </div>
          <div>
            <h2 class="text-subtitle-1 font-weight-medium">
              账号授权
            </h2>
            <div class="text-caption text--secondary">
              {{ authorizationDescription }}
            </div>
          </div>
        </div>

        <div
          v-if="isAuthorized"
          class="d-flex flex-wrap align-center authorization-success pa-3"
        >
          <v-icon
            color="success"
            class="mr-3"
          >
            $cloudCheck
          </v-icon>
          <div>
            <div class="font-weight-medium">
              已连接{{ providerName }}
            </div>
            <div class="text-caption text--secondary">
              {{ authorizationDetail }}
            </div>
          </div>
          <v-spacer />
          <app-btn
            small
            text
            :loading="authorizing"
            :disabled="jobRunning"
            @click="revokeAuthorization"
          >
            {{ provider === 'github'
              ? '删除令牌'
              : usesWebLogin ? '退出网页登录' : '解除授权' }}
          </app-btn>
        </div>

        <v-alert
          v-else-if="provider === 'github'"
          type="info"
          text
          dense
        >
          保存仓库信息和有效令牌后即可上传，不需要额外扫码授权。
        </v-alert>

        <template v-else-if="usesWebLogin">
          <v-alert
            v-if="webLoginNeedsAttention"
            :type="webLoginAlertType"
            text
            dense
            class="mb-3"
          >
            {{ webLogin.message || webLoginStateLabel }}
          </v-alert>

          <v-img
            v-if="webLogin.screenshot_data"
            :src="webLogin.screenshot_data"
            contain
            max-height="360"
            class="web-login-screenshot mb-4"
          />

          <v-row
            v-if="webLogin.state === 'verification_required' &&
              webLogin.challenge_type !== 'qr'"
          >
            <v-col
              cols="12"
              sm="7"
              md="5"
            >
              <v-text-field
                v-model.trim="verificationCode"
                :label="webLogin.challenge_type === 'sms' ? '短信验证码' : '页面验证码'"
                outlined
                dense
                maxlength="12"
                autocomplete="one-time-code"
                :disabled="authorizing"
                @keyup.enter="submitWebVerification"
              />
            </v-col>
            <v-col
              cols="12"
              sm="5"
              md="3"
            >
              <app-btn
                block
                color="primary"
                :loading="authorizing"
                :disabled="verificationCode.length < 4"
                @click="submitWebVerification"
              >
                提交验证
              </app-btn>
            </v-col>
          </v-row>

          <app-btn
            v-if="!webLoginRunning"
            color="primary"
            :disabled="jobRunning || !selectedModeConfigured"
            @click="startAuthorization"
          >
            <v-icon
              small
              left
            >
              $open
            </v-icon>
            {{ authorizationActionLabel }}
          </app-btn>
          <span
            v-if="!selectedModeConfigured"
            class="text-caption text--secondary ml-3"
          >先保存百度账号和密码</span>
        </template>

        <v-row
          v-else-if="oauthPending"
          align="center"
        >
          <v-col
            cols="12"
            sm="4"
            md="3"
          >
            <div
              v-if="status?.oauth.verification_url"
              class="oauth-qr"
            >
              <app-qr-code
                :value="status.oauth.verification_url"
                :size="180"
              />
            </div>
          </v-col>
          <v-col
            cols="12"
            sm="8"
            md="9"
          >
            <app-btn
              color="primary"
              :href="status?.oauth.verification_url"
              target="_blank"
              rel="noopener noreferrer"
            >
              <v-icon
                small
                left
              >
                $openInNew
              </v-icon>
              前往百度确认
            </app-btn>
            <v-row class="mt-3">
              <v-col
                cols="12"
                md="8"
              >
                <v-text-field
                  v-model.trim="authorizationCode"
                  label="百度授权码"
                  outlined
                  dense
                  maxlength="256"
                  autocomplete="one-time-code"
                  :disabled="authorizing"
                  @keyup.enter="submitBypyAuthorizationCode"
                />
              </v-col>
              <v-col
                cols="12"
                md="4"
              >
                <app-btn
                  block
                  color="primary"
                  :loading="authorizing"
                  :disabled="authorizationCode.length < 8"
                  @click="submitBypyAuthorizationCode"
                >
                  提交授权码
                </app-btn>
              </v-col>
            </v-row>
            <div class="text-caption text--secondary">
              百度页面确认后会显示授权码，将其粘贴到这里。
            </div>
          </v-col>
        </v-row>

        <v-alert
          v-else-if="bypyAuthorizationRunning"
          type="info"
          text
          dense
        >
          {{ status?.oauth.message || '正在处理百度 bypy 授权' }}
        </v-alert>

        <div v-else>
          <v-alert
            v-if="status?.oauth.state === 'error'"
            type="error"
            text
            dense
            class="mb-3"
          >
            {{ status.oauth.message || '百度 bypy 授权失败，请重新开始授权' }}
          </v-alert>
          <app-btn
            color="primary"
            :loading="authorizing"
            :disabled="jobRunning || (!selectedModeConfigured && !canSaveConfig)"
            @click="startAuthorization"
          >
            <v-icon
              small
              left
            >
              $open
            </v-icon>
            开始 bypy 授权
          </app-btn>
          <span
            v-if="!selectedModeConfigured"
            class="text-caption text--secondary ml-3"
          >{{ canSaveConfig ? '开始时会自动保存配置' : '先完成 bypy 目录配置' }}</span>
        </div>
      </section>

      <section class="cloud-section py-4 mb-4">
        <div class="section-heading d-flex align-center mb-4">
          <div class="step-index mr-3">
            3
          </div>
          <div>
            <h2 class="text-subtitle-1 font-weight-medium">
              创建配置备份
            </h2>
            <div class="text-caption text--secondary">
              百度命令行模式逐文件上传，并生成可直接阅读的备份说明与校验清单
            </div>
          </div>
        </div>

        <div class="auto-upload-settings mb-5 pb-5">
          <div class="d-flex flex-wrap align-center mb-3">
            <v-switch
              v-model="autoBackupEnabled"
              label="自动备份并上传"
              color="primary"
              hide-details
              class="mt-0"
              :disabled="savingConfig"
            />
            <v-spacer />
            <app-btn
              small
              outlined
              color="primary"
              :loading="savingConfig"
              :disabled="!canSaveConfig || autoBackupConfigSaved"
              @click="saveConfig"
            >
              <v-icon
                small
                left
              >
                $save
              </v-icon>
              保存自动设置
            </app-btn>
          </div>

          <template v-if="autoBackupEnabled">
            <v-btn-toggle
              v-model="autoBackupMode"
              mandatory
              color="primary"
              class="mb-4 schedule-mode-toggle"
            >
              <v-btn value="interval">
                按天数间隔
              </v-btn>
              <v-btn value="startup">
                每次开机后
              </v-btn>
            </v-btn-toggle>

            <v-row>
              <v-col
                v-if="autoBackupMode === 'interval'"
                cols="12"
                sm="6"
                md="4"
              >
                <v-text-field
                  v-model.number="autoBackupIntervalDays"
                  type="number"
                  min="1"
                  max="365"
                  step="1"
                  label="上传间隔（天）"
                  outlined
                  dense
                  :disabled="savingConfig"
                />
              </v-col>
              <v-col
                cols="12"
                sm="6"
                md="4"
              >
                <v-text-field
                  v-model.number="autoBackupStartupDelayMinutes"
                  type="number"
                  min="1"
                  max="1440"
                  step="1"
                  label="开机等待（分钟）"
                  outlined
                  dense
                  :disabled="savingConfig"
                />
              </v-col>
            </v-row>
          </template>

          <div class="d-flex align-center text-caption text--secondary">
            <v-icon
              small
              class="mr-2"
            >
              $clock
            </v-icon>
            {{ autoBackupStatusText }}
          </div>
          <div
            v-if="autoBackupLastSuccessText"
            class="text-caption text--secondary mt-1 ml-6"
          >
            {{ autoBackupLastSuccessText }}
          </div>
        </div>

        <div class="text-subtitle-2 font-weight-medium mb-3">
          手动上传
        </div>

        <v-textarea
          v-model="reason"
          outlined
          rows="3"
          counter="500"
          label="本次备份原因"
          hint="至少 10 个字符，例如：修改了挤出机步进参数并完成空载验证"
          persistent-hint
          :disabled="jobRunning"
        />

        <div class="text-caption text--secondary mb-2">
          备份内容
        </div>
        <div class="root-options mb-4">
          <v-checkbox
            v-for="root in config?.available_roots || []"
            :key="root.name"
            :input-value="selectedRoots.includes(root.name)"
            :label="rootLabel(root.name)"
            dense
            hide-details
            class="mt-0 mr-6"
            :disabled="jobRunning"
            @change="setRootSelected(root.name, $event)"
          />
        </div>

        <app-btn
          color="primary"
          :loading="creatingBackup"
          :disabled="!canCreateBackup"
          @click="createBackup"
        >
          <v-icon
            small
            left
          >
            $progressUpload
          </v-icon>
          立即备份并上传
        </app-btn>

        <div
          v-if="status?.active_job"
          class="job-progress mt-5"
        >
          <div class="d-flex justify-space-between text-body-2 mb-2">
            <span>{{ stageLabel(status.active_job.stage) }}</span>
            <span>{{ jobProgress(status.active_job) }}%</span>
          </div>
          <v-progress-linear
            :value="jobProgress(status.active_job)"
            color="primary"
            height="8"
            rounded
          />
          <div
            v-if="status.active_job.upload_total_bytes != null"
            class="d-flex flex-wrap justify-space-between text-caption mt-2"
          >
            <span>
              本次上传：{{ formatBytes(status.active_job.upload_total_bytes) }}
            </span>
            <span>
              已校验：{{ formatBytes(status.active_job.uploaded_bytes || 0) }} /
              {{ formatBytes(status.active_job.upload_total_bytes) }}
            </span>
          </div>
          <div
            v-if="status.active_job.upload_total_files != null"
            class="text-caption text--secondary mt-1"
          >
            文件 {{ status.active_job.uploaded_files || 0 }} /
            {{ status.active_job.upload_total_files }}
            <span v-if="status.active_job.current_file">
              · 当前：{{ status.active_job.current_file }}
            </span>
          </div>
        </div>
      </section>

      <section class="cloud-section py-4">
        <div class="d-flex align-center mb-3">
          <h2 class="text-subtitle-1 font-weight-medium">
            备份记录
          </h2>
          <v-spacer />
          <app-btn
            icon
            :loading="refreshing"
            @click="refresh"
          >
            <v-icon>$refresh</v-icon>
          </app-btn>
        </div>

        <div
          v-if="history.length === 0"
          class="empty-history py-8 text-center text--secondary"
        >
          尚无云端备份记录
        </div>
        <v-simple-table v-else>
          <thead>
            <tr>
              <th>时间</th>
              <th>原因</th>
              <th>目标</th>
              <th>状态</th>
              <th class="text-right">
                大小
              </th>
              <th class="text-right">
                操作
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="job in history"
              :key="job.job_id"
            >
              <td class="text-no-wrap">
                {{ formatDate(job.created_at) }}
              </td>
              <td class="reason-cell">
                <div>{{ job.reason }}</div>
                <div
                  v-if="job.trigger"
                  class="text-caption text--secondary"
                >
                  {{ triggerLabel(job.trigger) }}
                </div>
                <div
                  v-if="job.error"
                  class="text-caption error--text"
                >
                  {{ job.error }}
                </div>
              </td>
              <td class="text-no-wrap">
                {{ providerLabel(job.provider || 'baidu') }}
              </td>
              <td>
                <v-chip
                  x-small
                  :color="job.state === 'success' ? 'success' : 'error'"
                  outlined
                >
                  {{ job.state === 'success' ? '已上传' : '失败' }}
                </v-chip>
              </td>
              <td class="text-right text-no-wrap">
                {{ formatBytes(job.upload_total_bytes || job.archive_size) }}
              </td>
              <td class="text-right text-no-wrap">
                <app-btn
                  icon
                  small
                  :loading="downloadingJobId === job.job_id"
                  :disabled="!job.download_available ||
                    (jobRunning && downloadingJobId !== job.job_id)"
                  title="下载备份"
                  @click="downloadBackup(job)"
                >
                  <v-icon small>
                    $download
                  </v-icon>
                </app-btn>
              </td>
            </tr>
          </tbody>
        </v-simple-table>
      </section>
    </template>

    <v-overlay
      absolute
      :value="loading"
      opacity="0.08"
    >
      <v-progress-circular
        indeterminate
        color="primary"
      />
    </v-overlay>
  </v-container>
</template>

<script lang="ts">
import { Component, Vue } from 'vue-property-decorator'
import {
  createCloudBackup,
  getCloudBackupConfig,
  getCloudBackupHistory,
  getCloudBackupOAuthStatus,
  getCloudBackupStatus,
  getCloudBackupWebStatus,
  logoutCloudBackupGithub,
  logoutCloudBackupWeb,
  prepareCloudBackupDownload,
  revokeCloudBackupOAuth,
  saveCloudBackupConfig,
  startCloudBackupOAuth,
  startCloudBackupWebLogin,
  submitCloudBackupOAuthCode,
  submitCloudBackupWebVerification,
  type CloudBackupAuthMode,
  type CloudBackupAutoMode,
  type CloudBackupConfig,
  type CloudBackupConfigInput,
  type CloudBackupJob,
  type CloudBackupProvider,
  type CloudBackupStatus,
  type CloudBackupWebLogin,
} from '@/api/cloudBackup'
import { SocketActions } from '@/api/socketActions'
import downloadUrl from '@/util/download-url'

@Component({})
export default class CloudBackup extends Vue {
  loading = true
  refreshing = false
  savingConfig = false
  authorizing = false
  creatingBackup = false
  downloadingJobId = ''
  backendAvailable = true
  connectionStale = false
  errorMessage = ''
  status: CloudBackupStatus | null = null
  config: CloudBackupConfig | null = null
  history: CloudBackupJob[] = []
  provider: CloudBackupProvider = 'baidu'
  authMode: CloudBackupAuthMode = 'bypy'
  webUsername = ''
  webPassword = ''
  webRemotePath = '/3D打印机备份'
  bypyRemotePath = '/3D打印机备份'
  githubOwner = ''
  githubRepo = ''
  githubBranch = 'main'
  githubPath = 'printer-backups'
  githubToken = ''
  verificationCode = ''
  authorizationCode = ''
  selectedRoots: string[] = []
  autoBackupEnabled = false
  autoBackupMode: CloudBackupAutoMode = 'interval'
  autoBackupIntervalDays = 3
  autoBackupStartupDelayMinutes = 15
  reason = ''
  pollTimer: number | null = null
  polling = false
  destroyed = false

  get oauthPending (): boolean {
    return this.provider === 'baidu' &&
      this.authMode === 'bypy' &&
      this.status?.oauth.state === 'pending'
  }

  get bypyAuthorizationRunning (): boolean {
    return this.provider === 'baidu' &&
      this.authMode === 'bypy' &&
      ['starting', 'authorizing'].includes(this.status?.oauth.state || '')
  }

  get credentialsFromSecrets (): boolean {
    return this.config?.credential_source === 'moonraker_secrets'
  }

  get webLogin (): CloudBackupWebLogin {
    if (!this.status || this.status.provider !== this.provider) return { state: 'idle' }
    if (this.provider === 'baidu' && this.status.auth_mode !== this.authMode) {
      return { state: 'idle' }
    }
    return this.status.web_login
  }

  get webLoginRunning (): boolean {
    return ['starting', 'signing_in', 'verification_required'].includes(
      this.webLogin.state
    )
  }

  get usesWebLogin (): boolean {
    return this.provider === 'baidu' && this.authMode === 'web_password'
  }

  get webLoginNeedsAttention (): boolean {
    return !['idle', 'authorized'].includes(this.webLogin.state)
  }

  get webLoginAlertType (): 'info' | 'warning' | 'error' {
    if (['error', 'environment_missing'].includes(this.webLogin.state)) return 'error'
    if (['manual_verification', 'expired'].includes(this.webLogin.state)) return 'warning'
    return 'info'
  }

  get webLoginStateLabel (): string {
    const labels: Record<string, string> = {
      starting: '正在启动百度网页登录',
      signing_in: '正在登录百度账号',
      verification_required: '百度要求补充验证',
      manual_verification: '百度要求交互式安全验证',
      expired: '网页登录会话已过期',
      environment_missing: '打印机缺少网页登录运行环境',
      error: '百度网页登录失败',
    }
    return labels[this.webLogin.state] || ''
  }

  get activeRemotePath (): string {
    return this.authMode === 'web_password' ? this.webRemotePath : this.bypyRemotePath
  }

  set activeRemotePath (value: string) {
    if (this.authMode === 'web_password') this.webRemotePath = value
    else this.bypyRemotePath = value
  }

  get selectedModeConfigured (): boolean {
    return this.status?.provider === this.provider &&
      (this.provider !== 'baidu' || this.status?.auth_mode === this.authMode) &&
      this.status?.configured === true
  }

  get isAuthorized (): boolean {
    return !this.connectionStale &&
      this.status?.provider === this.provider &&
      (this.provider !== 'baidu' || this.status?.auth_mode === this.authMode) &&
      this.status?.authorized === true
  }

  get authorizationDescription (): string {
    if (this.provider === 'github') {
      return '使用细粒度令牌向指定仓库写入备份归档'
    }
    return this.authMode === 'web_password'
      ? '由打印机启动百度网页并使用保存的账号密码登录'
      : '一次授权后由打印机使用 bypy 命令行上传，不启动 Chromium'
  }

  get authorizationDetail (): string {
    if (this.provider === 'github') {
      return `归档将写入 ${this.githubOwner}/${this.githubRepo} 的 ${this.githubBranch} 分支`
    }
    return this.authMode === 'web_password'
      ? '网页登录 Cookie 保存在打印机中，会话失效后需要重新登录'
      : `逐文件备份将写入 我的应用数据/bypy${this.bypyRemotePath}`
  }

  get jobRunning (): boolean {
    return this.status?.active_job != null ||
      this.status?.download_in_progress === true
  }

  get authorizationActionLabel (): string {
    return '使用账号密码登录'
  }

  get canSaveConfig (): boolean {
    let credentialsValid = true
    let pathValid = true
    if (this.provider === 'baidu') {
      credentialsValid = this.authMode === 'web_password'
        ? this.webUsername.length >= 3 &&
          (this.webPassword.length >= 6 || this.config?.has_web_password === true)
        : this.config?.bypy_executable_available === true
      pathValid = this.authMode === 'web_password'
        ? this.webRemotePath.startsWith('/') && this.webRemotePath.length > 1
        : this.bypyRemotePath.startsWith('/') && this.bypyRemotePath.length > 1
      const pathParts = (this.authMode === 'web_password'
        ? this.webRemotePath
        : this.bypyRemotePath).slice(1).split('/')
      pathValid = pathValid &&
        pathParts.every(part => part.length > 0 && part !== '.' && part !== '..')
    } else {
      const githubName = /^[\w.-]{1,100}$/
      credentialsValid = this.githubToken.length >= 20 ||
        this.config?.has_github_token === true
      pathValid = githubName.test(this.githubOwner) &&
        githubName.test(this.githubRepo) &&
        this.githubBranch.length > 0 &&
        this.githubPath.length > 0
    }
    return credentialsValid &&
      pathValid &&
      this.selectedRoots.length > 0 &&
      this.autoBackupSettingsValid &&
      !this.jobRunning &&
      !this.authorizing &&
      !this.connectionStale
  }

  get autoBackupSettingsValid (): boolean {
    return Number.isInteger(this.autoBackupIntervalDays) &&
      this.autoBackupIntervalDays >= 1 &&
      this.autoBackupIntervalDays <= 365 &&
      Number.isInteger(this.autoBackupStartupDelayMinutes) &&
      this.autoBackupStartupDelayMinutes >= 1 &&
      this.autoBackupStartupDelayMinutes <= 1440
  }

  get canCreateBackup (): boolean {
    return this.isAuthorized &&
      !this.jobRunning &&
      this.reason.trim().length >= 10 &&
      this.reason.trim().length <= 500 &&
      this.selectedRoots.length > 0
  }

  get autoBackupConfigSaved (): boolean {
    return this.config?.auto_backup_enabled === this.autoBackupEnabled &&
      this.config?.auto_backup_mode === this.autoBackupMode &&
      this.config?.auto_backup_interval_days === this.autoBackupIntervalDays &&
      this.config?.auto_backup_startup_delay_minutes ===
        this.autoBackupStartupDelayMinutes
  }

  get autoBackupStatusText (): string {
    if (!this.autoBackupConfigSaved) return '设置尚未保存'
    const auto = this.status?.auto_backup
    if (!auto?.enabled) return '自动上传已关闭'
    if (auto.next_run_at != null) {
      const nextRun = `下次尝试：${this.formatDate(auto.next_run_at)}`
      return auto.message ? `${auto.message}；${nextRun}` : nextRun
    }
    return auto.message || '自动上传计划已启用'
  }

  get autoBackupLastSuccessText (): string {
    const lastSuccess = this.status?.auto_backup.last_success_at
    if (!this.autoBackupConfigSaved || lastSuccess == null) return ''
    return `最近成功上传：${this.formatDate(lastSuccess)}`
  }

  get statusLabel (): string {
    if (this.loading) return '正在连接'
    if (!this.backendAvailable) return '后端不可用'
    if (this.connectionStale) return '连接已中断'
    if (this.isAuthorized) return '已连接'
    if (this.selectedModeConfigured) return '等待授权'
    return '尚未配置'
  }

  get statusColor (): string | undefined {
    if (this.connectionStale) return 'error'
    if (this.isAuthorized) return 'success'
    return this.backendAvailable ? 'warning' : 'error'
  }

  get statusIcon (): string {
    return this.isAuthorized ? '$cloudCheck' : '$cloudAlert'
  }

  get providerName (): string {
    return this.providerLabel(this.provider)
  }

  mounted () {
    this.initialLoad()
    this.pollTimer = window.setInterval(() => {
      this.poll()
    }, 5000)
  }

  beforeDestroy () {
    this.destroyed = true
    if (this.pollTimer != null) window.clearInterval(this.pollTimer)
  }

  async initialLoad () {
    this.loading = true
    this.polling = true
    try {
      const [status, config, history] = await Promise.all([
        getCloudBackupStatus(),
        getCloudBackupConfig(),
        getCloudBackupHistory(),
      ])
      if (this.destroyed) return
      this.applyData(status, config, history)
      this.backendAvailable = true
      this.connectionStale = false
    } catch (error: any) {
      if (this.destroyed) return
      this.backendAvailable = error?.response?.status !== 404
      this.connectionStale = this.backendAvailable
      if (this.backendAvailable) this.setError(error)
    } finally {
      this.loading = false
      this.polling = false
    }
  }

  applyData (
    status: CloudBackupStatus,
    config: CloudBackupConfig,
    history: CloudBackupJob[]
  ) {
    this.status = status
    this.config = config
    this.history = history
    this.provider = config.provider || 'baidu'
    this.authMode = config.auth_mode
    this.webUsername = config.web_username
    this.webRemotePath = config.web_remote_path || '/3D打印机备份'
    this.bypyRemotePath = config.bypy_remote_path || '/3D打印机备份'
    this.githubOwner = config.github_owner || ''
    this.githubRepo = config.github_repo || ''
    this.githubBranch = config.github_branch || 'main'
    this.githubPath = config.github_path || 'printer-backups'
    this.selectedRoots = [...config.selected_roots]
    this.autoBackupEnabled = config.auto_backup_enabled
    this.autoBackupMode = config.auto_backup_mode
    this.autoBackupIntervalDays = config.auto_backup_interval_days
    this.autoBackupStartupDelayMinutes =
      config.auto_backup_startup_delay_minutes
  }

  async poll () {
    if (!this.backendAvailable || this.polling || this.destroyed) return
    this.polling = true
    try {
      const hadActiveJob = this.jobRunning
      const previousHistoryUpdate = this.status?.history_updated_at
      const status = await getCloudBackupStatus()
      const oauth = status.provider === 'baidu' &&
        status.auth_mode === 'bypy' &&
        ['starting', 'pending', 'authorizing'].includes(status.oauth.state)
        ? await getCloudBackupOAuthStatus()
        : status.oauth
      const webLogin = status.provider === 'baidu' &&
        status.auth_mode === 'web_password' &&
        !['idle', 'authorized'].includes(status.web_login.state)
        ? await getCloudBackupWebStatus()
        : status.web_login
      if (this.destroyed) return
      this.status = { ...status, oauth, web_login: webLogin }
      this.connectionStale = false
      const historyChanged = status.history_updated_at != null &&
        status.history_updated_at !== previousHistoryUpdate
      if (!status.active_job && (hadActiveJob || historyChanged)) {
        this.history = await getCloudBackupHistory()
      }
    } catch (error: any) {
      if (this.destroyed) return
      if (error?.response?.status === 404) this.backendAvailable = false
      else this.connectionStale = true
    } finally {
      this.polling = false
    }
  }

  async refresh () {
    if (this.polling || this.destroyed) return
    this.refreshing = true
    this.polling = true
    try {
      const [status, history] = await Promise.all([
        getCloudBackupStatus(),
        getCloudBackupHistory(),
      ])
      if (this.destroyed) return
      this.status = status
      this.history = history
      this.connectionStale = false
    } catch (error) {
      if (this.destroyed) return
      this.connectionStale = true
      this.setError(error)
    } finally {
      this.refreshing = false
      this.polling = false
    }
  }

  async saveConfig () {
    this.savingConfig = true
    try {
      const input: CloudBackupConfigInput = {
        provider: this.provider,
        auth_mode: this.authMode,
        web_remote_path: this.webRemotePath,
        bypy_remote_path: this.bypyRemotePath,
        github_owner: this.githubOwner,
        github_repo: this.githubRepo,
        github_branch: this.githubBranch,
        github_path: this.githubPath,
        selected_roots: this.selectedRoots,
        auto_backup_enabled: this.autoBackupEnabled,
        auto_backup_mode: this.autoBackupMode,
        auto_backup_interval_days: this.autoBackupIntervalDays,
        auto_backup_startup_delay_minutes:
          this.autoBackupStartupDelayMinutes,
        ...(this.provider === 'baidu' && this.authMode === 'web_password'
          ? { web_username: this.webUsername }
          : {}),
        ...(this.provider === 'baidu' && this.authMode === 'web_password' &&
          this.webPassword
          ? { web_password: this.webPassword }
          : {}),
        ...(this.provider === 'github' && this.githubToken
          ? { github_token: this.githubToken }
          : {}),
      }
      this.config = await saveCloudBackupConfig(input)
      this.webPassword = ''
      this.githubToken = ''
      this.status = await getCloudBackupStatus()
    } catch (error) {
      this.setError(error)
    } finally {
      this.savingConfig = false
    }
  }

  async startAuthorization () {
    this.authorizing = true
    try {
      if (!this.selectedModeConfigured) {
        if (!this.canSaveConfig) return
        await this.saveConfig()
        if (!this.selectedModeConfigured) return
      }
      if (this.usesWebLogin) {
        const webLogin = await startCloudBackupWebLogin()
        if (this.status) {
          this.status = {
            ...this.status,
            authorized: webLogin.state === 'authorized',
            web_login: webLogin,
          }
        }
      } else if (this.provider === 'baidu') {
        const oauth = await startCloudBackupOAuth()
        if (this.status) {
          this.status = {
            ...this.status,
            authorized: oauth.state === 'authorized',
            oauth,
          }
        }
      }
    } catch (error) {
      this.setError(error)
    } finally {
      this.authorizing = false
    }
  }

  async revokeAuthorization () {
    const isGithub = this.provider === 'github'
    const isWeb = this.usesWebLogin
    const target = this.providerName
    const confirmed = await this.$confirm(
      isGithub
        ? '将删除这台打印机保存的 GitHub 令牌，仓库配置仍会保留。是否继续？'
        : isWeb
          ? `将删除这台打印机保存的${target}网页 Cookie，其他配置仍会保留。是否继续？`
          : '仅删除这台打印机保存的百度授权令牌，是否继续？',
      {
        title: isGithub
          ? '删除 GitHub 令牌'
          : isWeb ? `退出${target}网页登录` : '解除百度网盘授权',
        color: 'card-heading',
        icon: '$warning',
      }
    )
    if (!confirmed) return
    this.authorizing = true
    try {
      if (isGithub) await logoutCloudBackupGithub()
      else if (isWeb) await logoutCloudBackupWeb()
      else await revokeCloudBackupOAuth()
      this.status = await getCloudBackupStatus()
    } catch (error) {
      this.setError(error)
    } finally {
      this.authorizing = false
    }
  }

  async submitWebVerification () {
    if (this.verificationCode.length < 4) return
    this.authorizing = true
    try {
      const webLogin = await submitCloudBackupWebVerification(this.verificationCode)
      this.verificationCode = ''
      if (this.status) {
        this.status = {
          ...this.status,
          authorized: webLogin.state === 'authorized',
          web_login: webLogin,
        }
      }
    } catch (error) {
      this.setError(error)
    } finally {
      this.authorizing = false
    }
  }

  async submitBypyAuthorizationCode () {
    if (this.authorizationCode.length < 8) return
    this.authorizing = true
    try {
      const oauth = await submitCloudBackupOAuthCode(this.authorizationCode)
      this.authorizationCode = ''
      if (this.status) this.status = { ...this.status, oauth }
    } catch (error) {
      this.setError(error)
    } finally {
      this.authorizing = false
    }
  }

  async createBackup () {
    this.creatingBackup = true
    try {
      const job = await createCloudBackup(this.reason.trim(), this.selectedRoots)
      if (this.status) this.status = { ...this.status, active_job: job }
      this.reason = ''
    } catch (error) {
      this.setError(error)
    } finally {
      this.creatingBackup = false
    }
  }

  async downloadBackup (job: CloudBackupJob) {
    if (!job.download_available || this.downloadingJobId) return
    this.downloadingJobId = job.job_id
    try {
      const prepared = await prepareCloudBackupDownload(job.job_id)
      const encodedPath = `${prepared.root}/${prepared.filename}`
        .replace(/[^/]+/g, value => encodeURIComponent(value))
      let url = `${this.$typedState.config.apiUrl}/server/files/${encodedPath}` +
        `?date=${Date.now()}`
      if (this.$typedState.auth.currentUser?.username !== '_TRUSTED_USER_') {
        url += `&token=${await SocketActions.accessOneshotToken()}`
      }
      downloadUrl(prepared.filename, url)
    } catch (error) {
      this.setError(error)
    } finally {
      this.downloadingJobId = ''
    }
  }

  rootLabel (name: string): string {
    return name === 'config' ? '打印机配置 (config)' : name
  }

  setRootSelected (name: string, selected: boolean) {
    this.selectedRoots = selected
      ? [...new Set([...this.selectedRoots, name])]
      : this.selectedRoots.filter(root => root !== name)
  }

  stageLabel (stage: string): string {
    const labels: Record<string, string> = {
      queued: '等待处理',
      creating_snapshot: '正在创建一致性文件快照',
      archiving: '正在创建归档',
      preparing_upload: '正在准备云端上传',
      uploading: '正在上传云端备份',
      verifying_upload: '正在校验上传结果',
      complete: '上传完成',
      cancelled: 'Moonraker 已停止',
      failed: '备份失败',
    }
    return labels[stage] || stage
  }

  triggerLabel (trigger: CloudBackupJob['trigger']): string {
    return trigger === 'automatic' ? '自动上传' : '手动上传'
  }

  providerLabel (provider: CloudBackupProvider): string {
    const labels: Record<CloudBackupProvider, string> = {
      baidu: '百度网盘',
      github: 'GitHub',
    }
    return labels[provider]
  }

  formatDate (timestamp: number): string {
    return this.$filters.formatDateTime(timestamp * 1000)
  }

  formatBytes (bytes?: number): string {
    if (bytes == null) return '-'
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  }

  jobProgress (job: CloudBackupJob): number {
    if (job.state === 'uploading' && job.upload_progress != null) {
      return job.upload_progress
    }
    return job.progress
  }

  setError (error: any) {
    this.errorMessage = error?.response?.data?.error?.message ||
      error?.response?.data?.error ||
      error?.message ||
      '操作失败，请检查 Moonraker 日志'
  }
}
</script>

<style lang="scss" scoped>
.cloud-backup {
  max-width: 1180px;
  position: relative;
}

.cloud-section {
  border-top: 1px solid rgba(128, 128, 128, 0.28);
}

.step-index {
  align-items: center;
  background: var(--v-primary-base);
  border-radius: 4px;
  color: #fff;
  display: flex;
  flex: 0 0 30px;
  font-size: 0.875rem;
  font-weight: 600;
  height: 30px;
  justify-content: center;
  width: 30px;
}

.authorization-success,
.empty-history {
  border: 1px solid rgba(128, 128, 128, 0.24);
  border-radius: 4px;
}

.auth-method-panel {
  background: rgba(128, 128, 128, 0.08);
  border-left: 3px solid var(--v-primary-base);
  padding: 12px 14px;
}

.auth-mode-toggle,
.provider-toggle {
  max-width: 100%;
}

.auth-mode-toggle .v-btn,
.provider-toggle .v-btn {
  min-height: 38px;
  white-space: normal;
}

.auto-upload-settings {
  border-bottom: 1px solid rgba(128, 128, 128, 0.24);
}

.schedule-mode-toggle {
  max-width: 100%;
}

.schedule-mode-toggle .v-btn {
  min-height: 36px;
  white-space: normal;
}

.web-login-screenshot {
  background: #fff;
  border: 1px solid rgba(128, 128, 128, 0.28);
  max-width: 640px;
}

.oauth-qr {
  align-items: center;
  background: #fff;
  border: 1px solid rgba(128, 128, 128, 0.28);
  border-radius: 4px;
  display: inline-flex;
  justify-content: center;
  min-height: 200px;
  min-width: 200px;
  padding: 10px;
}

.oauth-qr :deep(canvas),
.oauth-qr :deep(svg) {
  padding: 0;
}

.user-code {
  font-family: monospace;
  font-size: 1.75rem;
  font-weight: 600;
  letter-spacing: 0;
  overflow-wrap: anywhere;
}

.root-options {
  display: flex;
  flex-wrap: wrap;
  min-height: 32px;
}

.reason-cell {
  min-width: 260px;
  max-width: 620px;
  white-space: normal;
}

.job-progress {
  overflow-wrap: anywhere;
}

@media (max-width: 599px) {
  .cloud-backup {
    padding-left: 4px !important;
    padding-right: 4px !important;
  }

  .user-code {
    font-size: 1.35rem;
  }

  .auth-mode-toggle {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    width: 100%;
  }

  .provider-toggle {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    width: 100%;
  }

  .schedule-mode-toggle {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    width: 100%;
  }

  .reason-cell {
    min-width: 190px;
  }
}
</style>
