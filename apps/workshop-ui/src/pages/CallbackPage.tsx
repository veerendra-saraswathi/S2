import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";

import { ErrorBox, Loading } from "../components/ui";
import { makeUserManager, userManagerCallback } from "../lib/auth";

/** Keycloak redirects here after sign-in; finish the code exchange and go back. */
export function CallbackPage() {
  const navigate = useNavigate();
  const [error, setError] = useState<unknown>();
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return; // StrictMode runs effects twice; the code can be used once
    started.current = true;
    userManagerCallback(makeUserManager())
      .then((returnTo) => {
        // A full load, so AuthProvider reads the freshly stored user.
        window.location.replace(returnTo);
      })
      .catch((err: unknown) => setError(err));
  }, [navigate]);

  return <div className="mx-auto max-w-md p-8">{error ? <ErrorBox error={error} /> : <Loading label="Signing you in…" />}</div>;
}
