const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type Account = {
  id: string;
  cat_name: string;
  status: "ACTIVE" | "FROZEN" | "CLOSED";
  balance_minor: number;
  daily_limit_minor: number;
  created_at: string;
  // True for the two seed accounts (External Funding, Treasury) migration
  // 0002 creates. They're plumbing, not demo wallets -- the UI filters them
  // out of the sender/recipient pickers rather than showing a cat name next
  // to a balance in the negative millions.
  is_system: boolean;
};

export type LedgerEntry = {
  id: string;
  transfer_id: string;
  direction: "DEBIT" | "CREDIT";
  amount_minor: number;
  created_at: string;
};

export type Transfer = {
  id: string;
  idempotency_key: string;
  source_account_id: string;
  destination_account_id: string;
  amount_minor: number;
  currency: string;
  status: "PENDING" | "POSTED" | "FAILED";
  failure_code: string | null;
  failure_reason: string | null;
  created_at: string;
  completed_at: string | null;
};

/** Mirrors the {"error": {code, message, details}} envelope every backend
 * error uses. Thrown as-is so the UI can render the server's own code and
 * message instead of inventing its own copy.
 */
export class ApiError extends Error {
  code: string;
  details: Record<string, unknown>;

  constructor(code: string, message: string, details: Record<string, unknown>) {
    super(message);
    this.code = code;
    this.details = details;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const body = await res.json();
  if (!res.ok) {
    const err = body?.error ?? { code: "UNKNOWN_ERROR", message: "Something went wrong.", details: {} };
    throw new ApiError(err.code, err.message, err.details ?? {});
  }
  return body as T;
}

export function listAccounts(): Promise<{ items: Account[] }> {
  return request("/v1/accounts?limit=200");
}

export function getAccount(id: string): Promise<Account> {
  return request(`/v1/accounts/${id}`);
}

export function getTransactions(id: string): Promise<{ items: LedgerEntry[] }> {
  return request(`/v1/accounts/${id}/transactions?limit=20`);
}

export function createTransfer(params: {
  sourceAccountId: string;
  destinationAccountId: string;
  amountMinor: number;
  idempotencyKey: string;
}): Promise<Transfer> {
  return request("/v1/transfers", {
    method: "POST",
    headers: {
      "Idempotency-Key": params.idempotencyKey,
      "X-Cat-Id": params.sourceAccountId,
    },
    body: JSON.stringify({
      source_account_id: params.sourceAccountId,
      destination_account_id: params.destinationAccountId,
      amount_minor: params.amountMinor,
      currency: "TREATS",
    }),
  });
}

export function formatTreats(amountMinor: number): string {
  // Math.trunc(-0.5) is -0, which stringifies as "0" and silently drops the
  // sign. Split the sign off first and format the magnitude, so a negative
  // amount under one treat still prints with its minus sign.
  const sign = amountMinor < 0 ? "-" : "";
  const magnitude = Math.abs(amountMinor);
  const treats = Math.trunc(magnitude / 100);
  const whiskers = magnitude % 100;
  return `${sign}${treats}.${String(whiskers).padStart(2, "0")}`;
}
