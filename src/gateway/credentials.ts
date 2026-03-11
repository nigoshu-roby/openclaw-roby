import type { OpenClawConfig } from "../config/config.js";
import { containsEnvVarReference } from "../config/env-substitution.js";
import { hasConfiguredSecretInput, resolveSecretInputRef } from "../config/types.secrets.js";

export type ExplicitGatewayAuth = {
  token?: string;
  password?: string;
};

export type ResolvedGatewayCredentials = {
  token?: string;
  password?: string;
};

export type GatewayCredentialMode = "local" | "remote";
export type GatewayCredentialPrecedence = "env-first" | "config-first";
export type GatewayRemoteCredentialPrecedence = "remote-first" | "env-first";
export type GatewayRemoteCredentialFallback = "remote-env-local" | "remote-only";
type GatewaySecretDefaults = NonNullable<OpenClawConfig["secrets"]>["defaults"];

type GatewayConfiguredCredentialInput = {
  configured: boolean;
  value?: string;
  refPath?: string;
};

const GATEWAY_SECRET_REF_UNAVAILABLE_ERROR_CODE = "GATEWAY_SECRET_REF_UNAVAILABLE";

export class GatewaySecretRefUnavailableError extends Error {
  readonly code = GATEWAY_SECRET_REF_UNAVAILABLE_ERROR_CODE;
  readonly path: string;

  constructor(path: string) {
    super(
      [
        `${path} is configured as a secret reference but is unavailable in this command path.`,
        "Fix: set OPENCLAW_GATEWAY_TOKEN/OPENCLAW_GATEWAY_PASSWORD, pass explicit --token/--password,",
        "or run a gateway command path that resolves secret references before credential selection.",
      ].join("\n"),
    );
    this.name = "GatewaySecretRefUnavailableError";
    this.path = path;
  }
}

export function isGatewaySecretRefUnavailableError(
  error: unknown,
  expectedPath?: string,
): error is GatewaySecretRefUnavailableError {
  if (!(error instanceof GatewaySecretRefUnavailableError)) {
    return false;
  }
  if (!expectedPath) {
    return true;
  }
  return error.path === expectedPath;
}

export function trimToUndefined(value: unknown): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : undefined;
}

export function trimCredentialToUndefined(value: unknown): string | undefined {
  const trimmed = trimToUndefined(value);
  if (trimmed && containsEnvVarReference(trimmed)) {
    return undefined;
  }
  return trimmed;
}

function firstDefined(values: Array<string | undefined>): string | undefined {
  for (const value of values) {
    if (value) {
      return value;
    }
  }
  return undefined;
}

function throwUnresolvedGatewaySecretInput(path: string): never {
  throw new GatewaySecretRefUnavailableError(path);
}

function resolveConfiguredGatewayCredentialInput(params: {
  value: unknown;
  defaults?: GatewaySecretDefaults;
  path: string;
}): GatewayConfiguredCredentialInput {
  const ref = resolveSecretInputRef({
    value: params.value,
    defaults: params.defaults,
  }).ref;
  return {
    configured: hasConfiguredSecretInput(params.value, params.defaults),
    value: ref ? undefined : trimToUndefined(params.value),
    refPath: ref ? params.path : undefined,
  };
}

export function readGatewayTokenEnv(
  env: NodeJS.ProcessEnv = process.env,
  includeLegacyEnv = true,
): string | undefined {
  const primary = trimToUndefined(env.OPENCLAW_GATEWAY_TOKEN);
  if (primary) {
    return primary;
  }
  if (!includeLegacyEnv) {
    return undefined;
  }
  return trimToUndefined(env.CLAWDBOT_GATEWAY_TOKEN);
}

export function readGatewayPasswordEnv(
  env: NodeJS.ProcessEnv = process.env,
  includeLegacyEnv = true,
): string | undefined {
  const primary = trimToUndefined(env.OPENCLAW_GATEWAY_PASSWORD);
  if (primary) {
    return primary;
  }
  if (!includeLegacyEnv) {
    return undefined;
  }
  return trimToUndefined(env.CLAWDBOT_GATEWAY_PASSWORD);
}

export function hasGatewayTokenEnvCandidate(
  env: NodeJS.ProcessEnv = process.env,
  includeLegacyEnv = true,
): boolean {
  return Boolean(readGatewayTokenEnv(env, includeLegacyEnv));
}

export function hasGatewayPasswordEnvCandidate(
  env: NodeJS.ProcessEnv = process.env,
  includeLegacyEnv = true,
): boolean {
  return Boolean(readGatewayPasswordEnv(env, includeLegacyEnv));
}

export function resolveGatewayCredentialsFromValues(params: {
  configToken?: unknown;
  configPassword?: unknown;
  env?: NodeJS.ProcessEnv;
  includeLegacyEnv?: boolean;
  tokenPrecedence?: GatewayCredentialPrecedence;
  passwordPrecedence?: GatewayCredentialPrecedence;
}): ResolvedGatewayCredentials {
  const env = params.env ?? process.env;
  const includeLegacyEnv = params.includeLegacyEnv ?? true;
  const envToken = readGatewayTokenEnv(env, includeLegacyEnv);
  const envPassword = readGatewayPasswordEnv(env, includeLegacyEnv);
  const configToken = trimCredentialToUndefined(params.configToken);
  const configPassword = trimCredentialToUndefined(params.configPassword);
  const tokenPrecedence = params.tokenPrecedence ?? "env-first";
  const passwordPrecedence = params.passwordPrecedence ?? "env-first";

  const token =
    tokenPrecedence === "config-first"
      ? firstDefined([configToken, envToken])
      : firstDefined([envToken, configToken]);
  const password =
    passwordPrecedence === "config-first"
      ? firstDefined([configPassword, envPassword])
      : firstDefined([envPassword, configPassword]);

  return { token, password };
}

export function resolveGatewayCredentialsFromConfig(params: {
  cfg: OpenClawConfig;
  env?: NodeJS.ProcessEnv;
  explicitAuth?: ExplicitGatewayAuth;
  urlOverride?: string;
  urlOverrideSource?: "cli" | "env";
  modeOverride?: GatewayCredentialMode;
  includeLegacyEnv?: boolean;
  localTokenPrecedence?: GatewayCredentialPrecedence;
  localPasswordPrecedence?: GatewayCredentialPrecedence;
  remoteTokenPrecedence?: GatewayRemoteCredentialPrecedence;
  remotePasswordPrecedence?: GatewayRemoteCredentialPrecedence;
  remoteTokenFallback?: GatewayRemoteCredentialFallback;
  remotePasswordFallback?: GatewayRemoteCredentialFallback;
}): ResolvedGatewayCredentials {
  const env = params.env ?? process.env;
  const includeLegacyEnv = params.includeLegacyEnv ?? true;
  const explicitToken = trimToUndefined(params.explicitAuth?.token);
  const explicitPassword = trimToUndefined(params.explicitAuth?.password);
  if (explicitToken || explicitPassword) {
    return { token: explicitToken, password: explicitPassword };
  }
  if (trimToUndefined(params.urlOverride) && params.urlOverrideSource !== "env") {
    return {};
  }
  if (trimToUndefined(params.urlOverride) && params.urlOverrideSource === "env") {
    return resolveGatewayCredentialsFromValues({
      configToken: undefined,
      configPassword: undefined,
      env,
      includeLegacyEnv,
      tokenPrecedence: "env-first",
      passwordPrecedence: "env-first",
    });
  }

  const mode: GatewayCredentialMode =
    params.modeOverride ?? (params.cfg.gateway?.mode === "remote" ? "remote" : "local");
  const remote = params.cfg.gateway?.remote;
  const defaults = params.cfg.secrets?.defaults;
  const authMode = params.cfg.gateway?.auth?.mode;
  const envToken = readGatewayTokenEnv(env, includeLegacyEnv);
  const envPassword = readGatewayPasswordEnv(env, includeLegacyEnv);

  const localTokenInput = resolveConfiguredGatewayCredentialInput({
    value: params.cfg.gateway?.auth?.token,
    defaults,
    path: "gateway.auth.token",
  });
  const localPasswordInput = resolveConfiguredGatewayCredentialInput({
    value: params.cfg.gateway?.auth?.password,
    defaults,
    path: "gateway.auth.password",
  });
  const remoteTokenInput = resolveConfiguredGatewayCredentialInput({
    value: remote?.token,
    defaults,
    path: "gateway.remote.token",
  });
  const remotePasswordInput = resolveConfiguredGatewayCredentialInput({
    value: remote?.password,
    defaults,
    path: "gateway.remote.password",
  });
  const localTokenRef = localTokenInput.refPath;
  const localPasswordRef = localPasswordInput.refPath;
  const remoteTokenRef = remoteTokenInput.refPath;
  const remotePasswordRef = remotePasswordInput.refPath;
  const remoteToken = remoteTokenInput.value;
  const remotePassword = remotePasswordInput.value;
  const localToken = localTokenInput.value;
  const localPassword = localPasswordInput.value;

  const localTokenPrecedence =
    params.localTokenPrecedence ??
    (env.OPENCLAW_SERVICE_KIND === "gateway" ? "config-first" : "env-first");
  const localPasswordPrecedence = params.localPasswordPrecedence ?? "env-first";

  if (mode === "local") {
    const preferConfigToken = localTokenPrecedence === "config-first";
    const preferConfigPassword = localPasswordPrecedence === "config-first";
    const canUseEnvToken = Boolean(envToken);
    const canUseEnvPassword = Boolean(envPassword);
    const fallbackTokenConfigured =
      authMode !== "password" &&
      ((!preferConfigToken && canUseEnvToken) ||
        localTokenInput.configured ||
        remoteTokenInput.configured);
    const fallbackPasswordConfigured =
      authMode !== "token" &&
      ((!preferConfigPassword && canUseEnvPassword) ||
        localPasswordInput.configured ||
        remotePasswordInput.configured);

    if (fallbackTokenConfigured) {
      if (preferConfigToken && localTokenRef) {
        throwUnresolvedGatewaySecretInput(localTokenRef);
      }
      if (preferConfigToken && localTokenInput.configured && !localToken && !canUseEnvToken) {
        throwUnresolvedGatewaySecretInput("gateway.auth.token");
      }
      if (!canUseEnvToken) {
        if (localTokenRef) {
          throwUnresolvedGatewaySecretInput(localTokenRef);
        }
        if (localTokenInput.configured && !localToken && remoteTokenRef) {
          throwUnresolvedGatewaySecretInput(remoteTokenRef);
        }
        if (!localToken && !remoteToken && remoteTokenInput.configured) {
          throwUnresolvedGatewaySecretInput("gateway.remote.token");
        }
      }
    }

    if (fallbackPasswordConfigured) {
      if (preferConfigPassword && localPasswordRef) {
        throwUnresolvedGatewaySecretInput(localPasswordRef);
      }
      if (
        preferConfigPassword &&
        localPasswordInput.configured &&
        !localPassword &&
        !canUseEnvPassword
      ) {
        throwUnresolvedGatewaySecretInput("gateway.auth.password");
      }
      if (!canUseEnvPassword) {
        if (localPasswordRef) {
          throwUnresolvedGatewaySecretInput(localPasswordRef);
        }
        if (localPasswordInput.configured && !localPassword && remotePasswordRef) {
          throwUnresolvedGatewaySecretInput(remotePasswordRef);
        }
        if (!localPassword && !remotePassword && remotePasswordInput.configured) {
          throwUnresolvedGatewaySecretInput("gateway.remote.password");
        }
      }
    }

    const fallbackToken = localToken ?? remoteToken;
    const fallbackPassword = localPassword ?? remotePassword;
    return resolveGatewayCredentialsFromValues({
      configToken: fallbackToken,
      configPassword: fallbackPassword,
      env,
      includeLegacyEnv,
      tokenPrecedence: localTokenPrecedence,
      passwordPrecedence: localPasswordPrecedence,
    });
  }

  const remoteTokenFallback = params.remoteTokenFallback ?? "remote-env-local";
  const remotePasswordFallback = params.remotePasswordFallback ?? "remote-env-local";
  const remoteTokenPrecedence = params.remoteTokenPrecedence ?? "remote-first";
  const remotePasswordPrecedence = params.remotePasswordPrecedence ?? "env-first";

  const remoteTokenCandidates =
    remoteTokenFallback === "remote-only"
      ? [remoteToken]
      : remoteTokenPrecedence === "env-first"
        ? [envToken, remoteToken, localToken]
        : [remoteToken, envToken, localToken];
  const remotePasswordCandidates =
    remotePasswordFallback === "remote-only"
      ? [remotePassword]
      : remotePasswordPrecedence === "env-first"
        ? [envPassword, remotePassword, localPassword]
        : [remotePassword, envPassword, localPassword];

  if (
    remoteTokenFallback !== "remote-only" &&
    !envToken &&
    !remoteToken &&
    !localToken &&
    remoteTokenRef
  ) {
    throwUnresolvedGatewaySecretInput(remoteTokenRef);
  }
  if (
    remotePasswordFallback !== "remote-only" &&
    !envPassword &&
    !remotePassword &&
    !localPassword &&
    remotePasswordRef
  ) {
    throwUnresolvedGatewaySecretInput(remotePasswordRef);
  }

  return {
    token: firstDefined(remoteTokenCandidates),
    password: firstDefined(remotePasswordCandidates),
  };
}
