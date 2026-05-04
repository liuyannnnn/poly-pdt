import React, { FormEvent, useState } from "react";
import { loginWithPassword } from "../api/client";

interface LoginPageProps {
  onAuthenticated: () => void;
}

export function LoginPage({ onAuthenticated }: LoginPageProps) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      await loginWithPassword(password);
      onAuthenticated();
    } catch {
      setError("密码不正确或已过期");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen w-screen bg-gray-100 text-gray-900 flex items-center justify-center px-4">
      <main className="w-full max-w-sm bg-white border border-gray-200 rounded-lg shadow-sm p-6">
        <div className="mb-6">
          <h1 className="text-xl font-semibold text-gray-950">PDT2.1</h1>
        </div>
        <form onSubmit={submit} className="space-y-4">
          <label className="block">
            <span className="text-sm font-medium text-gray-700">密码</span>
            <input
              autoFocus
              className="mt-2 w-full h-10 rounded-md border border-gray-300 px-3 text-sm outline-none focus:border-gray-900 focus:ring-2 focus:ring-gray-200"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
            />
          </label>
          {error ? <div className="text-sm text-red-600">{error}</div> : null}
          <button
            className="w-full h-10 rounded-md bg-gray-950 text-white text-sm font-medium disabled:opacity-50"
            type="submit"
            disabled={!password || submitting}
          >
            {submitting ? "登录中" : "登录"}
          </button>
        </form>
      </main>
    </div>
  );
}
