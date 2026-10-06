import Keycloak from 'keycloak-js'

const url = import.meta.env.VITE_OIDC_URL || 'http://127.0.0.1:8080'
const realm = import.meta.env.VITE_OIDC_REALM || 'docucheck-demo'
const clientId = import.meta.env.VITE_OIDC_CLIENT_ID || 'docucheck-web'

export const identity = new Keycloak({ url, realm, clientId })

export async function initializeIdentity(): Promise<void> {
  const authenticated = await identity.init({
    onLoad: 'login-required',
    pkceMethod: 'S256',
    checkLoginIframe: false,
  })
  if (!authenticated) throw new Error('identity_unavailable')
}

export async function accessToken(): Promise<string> {
  await identity.updateToken(30)
  if (!identity.token) throw new Error('session_expired')
  return identity.token
}

export function signOut(): Promise<void> {
  return identity.logout({ redirectUri: location.origin })
}
