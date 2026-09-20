import { useCallback, useEffect, useState } from "react";
import CallSummary from "./CallSummary";
import { getJson, post } from "../../runtime/api.js";
import { fetchCustomers } from "../../runtime/customers.js";
import { formatDateTime, formatTime } from "../../runtime/format.js";
import { label, mapResult, toneOf } from "../../runtime/results.js";

const REFRESH_MS = 5000;

const when = (iso) => (iso ? formatDateTime(Date.parse(iso)) : "—");

function Badge({ status }) {
  return <span className={`badge badge-${toneOf(status)}`}>{label(status)}</span>;
}

// A business system does this over the API. This form is the same request,
// for an operator: it creates a call job and gives back the link to answer it.
function PlaceCall({ profiles, telephony, onPlaced }) {
  const [form, setForm] = useState({
    profile_id: profiles[0].id,
    channel: "web",
    customer_ref: "",
    name: "",
    phone: "",
    reason: "",
  });
  const [customers, setCustomers] = useState([]);
  const [placed, setPlaced] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const set = (key) => (event) => setForm({ ...form, [key]: event.target.value });

  useEffect(() => {
    let cancelled = false;

    fetchCustomers(form.profile_id).then((list) => {
      if (!cancelled) setCustomers(list);
    });

    return () => {
      cancelled = true;
    };
  }, [form.profile_id]);

  const changeProfile = (event) => {
    setCustomers([]); // never offer the previous profile's customers for this one
    setForm({ ...form, profile_id: event.target.value, customer_ref: "" });
  };

  // Choosing a customer fills in who to call, which can still be edited.
  const changeCustomer = (event) => {
    const chosen = customers.find((customer) => customer.ref === event.target.value);

    setForm({ ...form, customer_ref: event.target.value, name: chosen ? chosen.display_name : form.name });
  };

  const submit = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError(null);

    try {
      const response = await post("/api/call-jobs", {
        profile_id: form.profile_id,
        ...(form.customer_ref && { customer_ref: form.customer_ref }),
        channel: form.channel,
        callee: { name: form.name.trim(), phone: form.phone.trim() },
        reason: form.reason.trim(),
      });

      setPlaced(await response.json());
      onPlaced();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  };

  if (placed && !placed.answer_url) {
    return (
      <div className="history-form">
        <p>
          <strong>
            Call {placed.job_id}: {placed.status === "failed" ? "could not be placed" : `calling ${placed.callee.name} now`}.
          </strong>{" "}
          {placed.status === "failed"
            ? "The phone network refused it. Check the number and the server's Twilio settings."
            : "The call is on the phone. Its status and result appear in the table below when it ends."}
        </p>
        <div className="history-form-buttons">
          <button type="button" onClick={() => setPlaced(null)}>
            Place another
          </button>
        </div>
      </div>
    );
  }

  if (placed) {
    return (
      <div className="history-form">
        <p>
          <strong>Call {placed.job_id} is ringing.</strong> Send this link to {placed.callee.name}; when they open it
          and answer, the agent makes the call.
        </p>
        <input readOnly value={placed.answer_url} aria-label="Answer link" onFocus={(e) => e.target.select()} />
        <div className="history-form-buttons">
          <button type="button" onClick={() => navigator.clipboard?.writeText(placed.answer_url)}>
            Copy link
          </button>
          <button type="button" onClick={() => window.open(placed.answer_url, "_blank", "noopener")}>
            Open it here
          </button>
          <button type="button" onClick={() => setPlaced(null)}>
            Place another
          </button>
        </div>
      </div>
    );
  }

  return (
    <form className="history-form" onSubmit={submit}>
      <label htmlFor="pc-profile">Profile</label>
      <select id="pc-profile" value={form.profile_id} onChange={changeProfile}>
        {profiles.map((profile) => (
          <option key={profile.id} value={profile.id}>
            {profile.name}
          </option>
        ))}
      </select>

      {telephony && (
        <>
          <label htmlFor="pc-channel">How</label>
          <select id="pc-channel" value={form.channel} onChange={set("channel")}>
            <option value="web">Web link (the person opens it and answers)</option>
            <option value="phone">Phone call (rings their number)</option>
          </select>
        </>
      )}

      {customers.length > 0 && (
        <>
          <label htmlFor="pc-customer">Customer data</label>
          <select id="pc-customer" value={form.customer_ref} onChange={changeCustomer}>
            <option value="">Demo data</option>
            {customers.map((customer) => (
              <option key={customer.ref} value={customer.ref}>
                {customer.display_name}
              </option>
            ))}
          </select>
        </>
      )}

      <label htmlFor="pc-name">Who to call</label>
      <input id="pc-name" value={form.name} onChange={set("name")} placeholder="Priya Sharma" required />

      <label htmlFor="pc-phone">
        Phone{form.channel === "phone" ? " (international format, this number will be dialled)" : ""}
      </label>
      <input id="pc-phone" value={form.phone} onChange={set("phone")} placeholder="+91 98765 43210" required />

      <label htmlFor="pc-reason">Why you are calling (said to them)</label>
      <input
        id="pc-reason"
        value={form.reason}
        onChange={set("reason")}
        placeholder="unusual activity on your credit card"
        required
      />

      {error && (
        <p className="voice-notice" role="alert">
          {error}
        </p>
      )}

      <div className="history-form-buttons">
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Placing..." : "Place call"}
        </button>
      </div>
    </form>
  );
}

export default function CallHistory({ serverAvailable, telephony, profiles }) {
  const [jobs, setJobs] = useState([]);
  const [calls, setCalls] = useState([]);
  const [error, setError] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [detail, setDetail] = useState(null);

  const load = useCallback(async () => {
    try {
      const [jobRows, callRows] = await Promise.all([
        getJson("/api/call-jobs?limit=30"),
        getJson("/api/calls?limit=30"),
      ]);

      setJobs(jobRows);
      setCalls(callRows);
      setError(null);
    } catch (failure) {
      setError(failure.message);
    }
  }, []);

  useEffect(() => {
    if (!serverAvailable) return undefined;

    const first = setTimeout(load, 0);
    const timer = setInterval(load, REFRESH_MS);

    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [load, serverAvailable]);

  const open = async (callId) => {
    try {
      setDetail(mapResult(await getJson(`/api/calls/${encodeURIComponent(callId)}/result`)));
    } catch (failure) {
      setError(failure.message);
    }
  };

  if (!serverAvailable) {
    return (
      <section className="history-page">
        <h2>Call History</h2>
        <p className="callee-note">
          Call history is kept by the server. Start the backend (and sign in) to see past calls and to place
          outbound calls.
        </p>
      </section>
    );
  }

  if (detail) {
    return (
      <section className="history-page">
        <button className="history-back" onClick={() => setDetail(null)}>
          ← Back to history
        </button>
        <CallSummary summary={detail} />
      </section>
    );
  }

  return (
    <section className="history-page">
      <div className="history-head">
        <h2>Call History</h2>
        <div className="history-form-buttons">
          <button onClick={load}>Refresh</button>
          <button className="primary" onClick={() => setShowForm(!showForm)}>
            {showForm ? "Close" : "Place a call"}
          </button>
        </div>
      </div>

      {error && (
        <p className="voice-notice" role="alert">
          {error}
        </p>
      )}

      {showForm && <PlaceCall profiles={profiles} telephony={telephony} onPlaced={load} />}

      <h3>Outbound jobs</h3>
      {jobs.length === 0 ? (
        <p className="callee-note">No outbound calls yet.</p>
      ) : (
        <table className="history-table">
          <thead>
            <tr>
              <th>Created</th>
              <th>Via</th>
              <th>Who</th>
              <th>Reason</th>
              <th>Status</th>
              <th>Result sent</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.job_id}>
                <td>{when(job.created_at)}</td>
                <td>{job.channel}</td>
                <td>
                  {job.callee.name} <span className="muted">{job.callee.phone}</span>
                </td>
                <td>{job.reason}</td>
                <td>
                  <Badge status={job.status} />
                </td>
                <td>{job.callback.status === "none" ? "—" : job.callback.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h3>Calls</h3>
      {calls.length === 0 ? (
        <p className="callee-note">No finished calls yet.</p>
      ) : (
        <table className="history-table">
          <thead>
            <tr>
              <th>When</th>
              <th>Direction</th>
              <th>Profile</th>
              <th>With</th>
              <th>Outcome</th>
              <th>Duration</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {calls.map((call) => (
              <tr key={call.call_id}>
                <td>{when(call.started_at)}</td>
                <td>
                  {call.direction} <span className="muted">{call.channel}</span>
                </td>
                <td>{call.profile_name}</td>
                <td>{call.callee_name ?? "—"}</td>
                <td>
                  <Badge status={call.outcome} />
                </td>
                <td>{formatTime(call.duration_seconds ?? 0)}</td>
                <td>
                  <button className="history-open" onClick={() => open(call.call_id)}>
                    View
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
