import { UserManager, WebStorageStateStore } from "oidc-client-ts";

const runtimeConfig = globalThis.__RAGKB_CONFIG__ ?? {};
const oidc = runtimeConfig.oidc ?? {};
export const oidcEnabled = oidc.enabled === true;

let manager;
function userManager() {
  if (!oidcEnabled) return null;
  manager ??= new UserManager({
    authority: oidc.authority,
    client_id: oidc.clientId,
    redirect_uri: oidc.redirectUri,
    post_logout_redirect_uri: oidc.postLogoutRedirectUri || oidc.redirectUri,
    response_type: "code",
    scope: oidc.scope || "openid profile email",
    automaticSilentRenew: true,
    userStore: new WebStorageStateStore({ store: globalThis.sessionStorage }),
  });
  return manager;
}

export async function initializeAuth() {
  const current = userManager();
  if (!current) return null;
  const url = new URL(globalThis.location.href);
  if (url.searchParams.has("code") && url.searchParams.has("state")) {
    const user = await current.signinRedirectCallback();
    globalThis.history.replaceState({}, document.title, "/");
    return user;
  }
  return current.getUser();
}

export async function accessToken() {
  const current = userManager();
  if (!current) return null;
  const user = await current.getUser();
  return user && !user.expired ? user.access_token : null;
}

export async function signIn() {
  const current = userManager();
  if (current) await current.signinRedirect();
}

export async function signOut() {
  const current = userManager();
  if (current) await current.signoutRedirect();
}
