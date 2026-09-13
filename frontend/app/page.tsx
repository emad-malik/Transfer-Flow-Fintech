"use client";

import { useEffect, useState } from "react";
import {
  Account,
  ApiError,
  LedgerEntry,
  Transfer,
  createTransfer,
  formatTreats,
  getAccount,
  getTransactions,
  listAccounts,
} from "@/lib/api";

/** Parses a decimal treats string ("12.50") into integer minor units (1250)
 * without ever going through a float, so client-side rounding can't disagree
 * with the server's integer-only contract.
 */
function parseTreatsToMinor(input: string): number | null {
  const trimmed = input.trim();
  if (!/^\d+(\.\d{1,2})?$/.test(trimmed)) return null;
  const [wholePart, fracPart = ""] = trimmed.split(".");
  const paddedFrac = (fracPart + "00").slice(0, 2);
  return parseInt(wholePart, 10) * 100 + parseInt(paddedFrac, 10);
}

function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

export default function SendTreatsPage() {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [loadingAccounts, setLoadingAccounts] = useState(true);
  const [sourceId, setSourceId] = useState("");
  const [destinationId, setDestinationId] = useState("");
  const [amount, setAmount] = useState("");
  const [idempotencyKey, setIdempotencyKey] = useState(newIdempotencyKey());
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<Transfer | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [sourceAccount, setSourceAccount] = useState<Account | null>(null);
  const [destinationAccount, setDestinationAccount] = useState<Account | null>(null);
  const [transactions, setTransactions] = useState<LedgerEntry[]>([]);

  useEffect(() => {
    listAccounts()
      .then((res) => {
        setAccounts(res.items);
        if (res.items.length >= 2) {
          setSourceId(res.items[0].id);
          setDestinationId(res.items[1].id);
        }
      })
      .catch(() => setAccounts([]))
      .finally(() => setLoadingAccounts(false));
  }, []);

  // A new source/destination/amount is a new attempt: fresh idempotency key.
  useEffect(() => {
    setIdempotencyKey(newIdempotencyKey());
  }, [sourceId, destinationId, amount]);

  async function refreshAccountState() {
    if (sourceId) setSourceAccount(await getAccount(sourceId).catch(() => null));
    if (destinationId) setDestinationAccount(await getAccount(destinationId).catch(() => null));
    if (sourceId) {
      const page = await getTransactions(sourceId).catch(() => ({ items: [] }));
      setTransactions(page.items);
    }
  }

  useEffect(() => {
    refreshAccountState();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceId, destinationId]);

  const amountMinor = parseTreatsToMinor(amount);
  const canSubmit =
    !submitting &&
    !!sourceId &&
    !!destinationId &&
    sourceId !== destinationId &&
    amountMinor !== null &&
    amountMinor > 0;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit || amountMinor === null) return;

    setSubmitting(true);
    setError(null);
    setResult(null);

    let keepSameKeyForRetry = false;
    try {
      const transfer = await createTransfer({
        sourceAccountId: sourceId,
        destinationAccountId: destinationId,
        amountMinor,
        idempotencyKey,
      });
      setResult(transfer);
      await refreshAccountState();
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err);
        // A network-ish/server-fault response is exactly the case an
        // idempotency key exists for: we don't know if the server actually
        // processed it, so a manual retry must reuse the same key rather
        // than risk a second real attempt. Any definitive business or
        // validation error (bad amount, insufficient funds, etc.) is a known
        // outcome -- fixing the input and submitting again should be a new
        // attempt, so a fresh key is issued.
        keepSameKeyForRetry = err.code === "INTERNAL_ERROR";
      } else {
        setError(new ApiError("NETWORK_ERROR", "Could not reach the server. Check your connection and retry.", {}));
        keepSameKeyForRetry = true;
      }
    } finally {
      setSubmitting(false);
      if (!keepSameKeyForRetry) {
        setIdempotencyKey(newIdempotencyKey());
      }
    }
  }

  return (
    <main>
      <h1>MeowPay</h1>
      <p className="subtitle">Send treats between cat wallets.</p>

      <div className="balances">
        <div className="balance-box">
          <div className="label">Sender balance</div>
          <div className="value">
            {sourceAccount ? `${formatTreats(sourceAccount.balance_minor)} treats` : "-"}
          </div>
        </div>
        <div className="balance-box">
          <div className="label">Recipient balance</div>
          <div className="value">
            {destinationAccount ? `${formatTreats(destinationAccount.balance_minor)} treats` : "-"}
          </div>
        </div>
      </div>

      <form className="card" onSubmit={handleSubmit}>
        <label htmlFor="source">From</label>
        <select id="source" value={sourceId} onChange={(e) => setSourceId(e.target.value)} disabled={loadingAccounts}>
          <option value="" disabled>
            Select a sender
          </option>
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.cat_name} ({formatTreats(a.balance_minor)} treats)
            </option>
          ))}
        </select>

        <label htmlFor="destination">To</label>
        <select
          id="destination"
          value={destinationId}
          onChange={(e) => setDestinationId(e.target.value)}
          disabled={loadingAccounts}
        >
          <option value="" disabled>
            Select a recipient
          </option>
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.cat_name}
            </option>
          ))}
        </select>

        <label htmlFor="amount">Amount (treats)</label>
        <input
          id="amount"
          type="number"
          min="0"
          step="0.01"
          placeholder="0.00"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
        />

        <button type="submit" disabled={!canSubmit}>
          {submitting ? "Sending..." : "Send treats"}
        </button>

        {error && (
          <div className="message error">
            <strong>{error.code}</strong>: {error.message}
          </div>
        )}
        {result && result.status === "POSTED" && (
          <div className="message success">
            Sent! Transfer <code>{result.id}</code> posted.
          </div>
        )}
        {result && result.status === "FAILED" && (
          <div className="message error">
            <strong>{result.failure_code}</strong>: {result.failure_reason}
          </div>
        )}
      </form>

      <div className="card">
        <h2 style={{ fontSize: "1rem", marginTop: 0 }}>Sender&apos;s recent transactions</h2>
        {transactions.length === 0 && <p className="empty">No transactions yet.</p>}
        {transactions.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Direction</th>
                <th>Amount</th>
                <th>When</th>
              </tr>
            </thead>
            <tbody>
              {transactions.map((t) => (
                <tr key={t.id}>
                  <td className={t.direction === "DEBIT" ? "direction-debit" : "direction-credit"}>
                    {t.direction}
                  </td>
                  <td>{formatTreats(t.amount_minor)}</td>
                  <td>{new Date(t.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </main>
  );
}
