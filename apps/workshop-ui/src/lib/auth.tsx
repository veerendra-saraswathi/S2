// Sign-in with Keycloak (authorization code + PKCE, client s2-workshop).

import { User, UserManager, WebStorageStateStore } from "oidc-client-ts";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { Api } from "./api";

const AUTHORITY = import.meta.env.VITE_OIDC_AUTHORITY ?? "http://localhost:8080/realms/s2";
const CLIENT_ID = import.meta.env.VITE_OIDC_CLIENT_ID ?? "s2-workshop";
export const CALLBACK_PATH = "/callback";

export function makeUserManager(origin = window.location.origin): UserManager {
  return new UserManager({
    authority: AUTHORITY,
    client_id: CLIENT_ID,
    redirect_uri: `${origin}${CALLBACK_PATH}`,
    post_logout_redirect_uri: `${origin}/`,
    response_type: "code",
    scope: "openid",
    // Session storage: a token does not outlive the tab, and other sites cannot read it.
    userStore: new WebStorageStateStore({ store: window.sessionStorage }),
    automaticSilentRenew: false,
  });
}

/** Realm roles from the access token. Display only: the API decides what is allowed. */
export function rolesOf(accessToken: string | undefined): string[] {
  if (!accessToken) return [];
  try {
    const payload = accessToken.split(".")[1];
    const json = JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/")));
    const roles = json?.realm_access?.roles;
    return Array.isArray(roles) ? roles.map(String) : [];
  } catch {
    return [];
  }
}

export type Auth = {
  user: User | null;
  ready: boolean;
  roles: string[];
  username: string;
  api: Api;
  login: (returnTo?: string) => Promise<void>;
  logout: () => Promise<void>;
  hasRole: (...roles: string[]) => boolean;
};

const AuthContext = createContext<Auth | null>(null);

export function AuthProvider({ children, manager }: { children: ReactNode; manager?: UserManager }) {
  const userManager = useMemo(() => manager ?? makeUserManager(), [manager]);
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void userManager.getUser().then((found) => {
      if (cancelled) return;
      setUser(found && !found.expired ? found : null);
      setReady(true);
    });
    const onLoaded = (loaded: User) => setUser(loaded);
    const onUnloaded = () => setUser(null);
    userManager.events.addUserLoaded(onLoaded);
    userManager.events.addUserUnloaded(onUnloaded);
    userManager.events.addAccessTokenExpired(onUnloaded);
    return () => {
      cancelled = true;
      userManager.events.removeUserLoaded(onLoaded);
      userManager.events.removeUserUnloaded(onUnloaded);
      userManager.events.removeAccessTokenExpired(onUnloaded);
    };
  }, [userManager]);

  const login = useCallback(
    (returnTo?: string) => userManager.signinRedirect({ state: { returnTo: returnTo ?? window.location.pathname } }),
    [userManager],
  );
  const logout = useCallback(async () => {
    await userManager.removeUser();
    setUser(null);
  }, [userManager]);

  const value = useMemo<Auth>(() => {
    const roles = rolesOf(user?.access_token);
    return {
      user,
      ready,
      roles,
      username: String(user?.profile?.preferred_username ?? user?.profile?.sub ?? ""),
      api: new Api(() => user?.access_token ?? null),
      login,
      logout,
      hasRole: (...wanted: string[]) => wanted.some((role) => roles.includes(role)),
    };
  }, [user, ready, login, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): Auth {
  const auth = useContext(AuthContext);
  if (!auth) throw new Error("useAuth outside AuthProvider");
  return auth;
}

export { AuthContext, userManagerCallback };

async function userManagerCallback(manager: UserManager): Promise<string> {
  const user = await manager.signinRedirectCallback();
  const state = user.state as { returnTo?: string } | undefined;
  return state?.returnTo && state.returnTo !== CALLBACK_PATH ? state.returnTo : "/";
}
