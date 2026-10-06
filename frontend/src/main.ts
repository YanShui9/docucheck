import { createApp } from 'vue'
import App from './App.vue'
import { initializeIdentity } from './auth'
import './style.css'

const target = document.getElementById('app')
if (!target) throw new Error('app_root_missing')

initializeIdentity()
  .then(() => createApp(App).mount(target))
  .catch(() => {
    target.innerHTML = '<main class="startup-error"><h1>无法连接身份服务</h1><p>请确认本地 OIDC 服务已启动，并刷新页面。</p></main>'
  })
