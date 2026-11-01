#!/usr/bin/env python3
"""
analyze_flexplay.py - offline analysis for the FlexPlay glove (SSRP project).

Subcommands
  synth   make synthetic Signal-Lab CSVs so the whole pipeline can be rehearsed before the sensors arrive
  noise   noise floor, PSD, drift, autocorrelation, mains check, SNR      (rubric: noise identification)
  filters compare candidate filters: residual noise vs. lag (Pareto), Bode plot   (rubric: filtering, FFT/MAV/RMS)
  fsrcal  fit force = k / R from known weights, prints k to paste into Therapist settings
  stats   paired comparison raw vs processed: t-test, Cohen's dz, Wilcoxon, order effect

Needs numpy, scipy, matplotlib.   Examples:
  python3 analyze_flexplay.py synth --out demo
  python3 analyze_flexplay.py noise demo/lab_still.csv --signal demo/lab_move.csv --out demo/out
  python3 analyze_flexplay.py filters demo/lab_move.csv --rest demo/lab_still.csv --channel flex_index --out demo/out
  python3 analyze_flexplay.py stats demo/paired.csv --a raw --b filtered --out demo/out
"""
import argparse, csv, os, sys
import numpy as np
from scipy import signal, stats
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

CH = ["flex_thumb", "flex_index", "flex_middle", "flex_ring", "flex_little",
      "fsr_index", "fsr_middle", "fsr_ring", "fsr_little", "fsr_ball"]

# ------------------------------------------------------------------ io
def load_lab(path):
    meta, hdr, rows = {}, None, []
    with open(path, newline="") as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("#"):
                s = line[1:].strip()
                if "=" in s:
                    for tok in s.split(" "):
                        if "=" in tok:
                            k, v = tok.split("=", 1); meta[k.strip()] = v.strip()
                    if s.startswith("condition="):
                        meta["condition"] = s.split("=", 1)[1]
                continue
            if hdr is None:
                hdr = line.split(","); continue
            if line.strip():
                rows.append(line.split(","))
    if hdr is None or not rows:
        sys.exit(f"{path}: no data rows")
    a = np.array([[float(x) if x != "" else np.nan for x in r] for r in rows])
    col = {h: i for i, h in enumerate(hdr)}
    d = dict(meta=meta, t=a[:, col["t_s"]], seq=a[:, col["seq"]])
    d["raw"] = np.column_stack([a[:, col["raw_" + k]] for k in CH])
    d["filt"] = np.column_stack([a[:, col["filt_" + k]] for k in CH])
    dt = np.diff(d["t"])
    d["fs"] = 1.0 / np.median(dt) if len(dt) else 100.0
    d["dt"] = dt
    return d

def resample_uniform(t, x, fs):
    """Samples arrive with jitter and the odd gap; analysis needs a uniform grid."""
    tg = np.arange(t[0], t[-1], 1.0 / fs)
    return tg, np.interp(tg, t, x)

def chan(name):
    if name in CH: return CH.index(name)
    try: return int(name)
    except ValueError: sys.exit(f"unknown channel {name}; use one of {CH}")

def save_fig(fig, out, name):
    os.makedirs(out, exist_ok=True)
    p = os.path.join(out, name); fig.savefig(p, dpi=130, bbox_inches="tight"); plt.close(fig); print("  wrote", p)

# ------------------------------------------------------------------ noise
def noise_stats(x, fs):
    x = x[np.isfinite(x)]
    n = np.arange(len(x)); k = np.polyfit(n / fs, x, 1)          # drift in mV/s
    r = x - np.polyval(k, n / fs)                                  # detrended
    ac1 = np.corrcoef(r[:-1], r[1:])[0, 1] if len(r) > 3 and np.std(r) > 0 else np.nan
    sg = 1.4826 * np.median(np.abs(r - np.median(r)))               # robust sigma (MAD): not inflated by contact glitches
    sp = np.abs(r - np.median(r)) > 5 * max(sg, 1e-9)               # >5 sigma = spike / glitch
    clean = r[~sp].std() if (~sp).sum() > 3 else r.std()
    return dict(mean=x.mean(), rms_ac=np.sqrt(np.mean((x - x.mean()) ** 2)), mav_ac=np.mean(np.abs(x - x.mean())),
                pp=x.max() - x.min(), drift_mV_per_min=k[0] * 60, rms_detr=clean, rms_all=r.std(), spikes=int(sp.sum()), ac1=ac1)

def cmd_noise(a):
    d = load_lab(a.csv); fs = a.fs or round(d["fs"])
    dur = d["t"][-1] - d["t"][0]
    print(f"{a.csv}: condition='{d['meta'].get('condition','?')}'  {len(d['t'])} samples  {dur:.1f} s  effective fs {d['fs']:.1f} Hz")
    if dur < 299: print(f"  WARNING: {dur:.0f} s < 300 s. The brief asks for at least 5 minutes per condition.")
    gaps = (d["dt"] > 2.5 / fs).sum()
    seq = d["seq"][np.isfinite(d["seq"])]
    lost = int(np.sum(np.diff(seq)[(np.diff(seq) > 1) & (np.diff(seq) < 1000)] - 1)) if len(seq) > 2 else 0
    print(f"  timing: max gap {d['dt'].max()*1000:.0f} ms, gaps>{2.5/fs*1000:.0f} ms: {gaps}, lost sequence numbers: {lost}, jitter std {d['dt'].std()*1000:.2f} ms")
    rows = []; fig, ax = plt.subplots(2, 5, figsize=(17, 6), sharex=True); ax = ax.ravel()
    sig = load_lab(a.signal) if a.signal else None
    print(f"\n  {'channel':<13}{'mean mV':>9}{'RMS clean':>10}{'RMS all':>8}{'MAV(ac)':>8}{'spikes':>7}{'drift/min':>10}{'lag1 ac':>8}{'mains*':>8}{'SNR dB':>8}")
    for i, name in enumerate(CH):
        tg, x = resample_uniform(d["t"], d["raw"][:, i], fs)
        if np.allclose(x, 0):
            print(f"  {name:<13}  (all zero: sensor not connected or no touch)"); continue
        s = noise_stats(x, fs)
        f, P = signal.welch(x - x.mean(), fs=fs, nperseg=min(len(x), 1024), detrend="linear")
        # fs=100 Hz: 50 Hz mains sits exactly at Nyquist, 100/150/200 Hz fold to 0/50/0 Hz. So look at the top bins AND at DC drift.
        top = P[f >= fs * 0.4].sum() / max(P.sum(), 1e-30)
        snr = np.nan
        if sig is not None:
            ts, xs = resample_uniform(sig["t"], sig["raw"][:, i], fs)
            snr = 20 * np.log10(np.std(xs) / max(s["rms_detr"], 1e-9))
        rows.append([name, s["mean"], s["rms_detr"], s["rms_all"], s["mav_ac"], s["pp"], s["spikes"], s["drift_mV_per_min"], s["ac1"], top, snr])
        print(f"  {name:<13}{s['mean']:9.1f}{s['rms_detr']:10.2f}{s['rms_all']:8.2f}{s['mav_ac']:8.2f}{s['spikes']:7d}{s['drift_mV_per_min']:10.2f}{s['ac1']:8.2f}{top*100:7.1f}%{snr:8.1f}")
        ax[i].semilogy(f, P + 1e-12, lw=.8); ax[i].set_title(name, fontsize=9); ax[i].grid(alpha=.3)
        if i >= 5: ax[i].set_xlabel("Hz")
    print("  RMS clean = noise after removing >5-sigma spikes (what a filter can reduce by averaging); RMS all includes the glitches (what the median despiker is for).")
    print("  * mains = share of noise power in 40-50 Hz. At fs=100 Hz a spike at the right edge of the PSD (50 Hz) or a slow 'beat' (100 Hz folds to DC) means mains pickup;")
    print("    lag-1 autocorrelation near 0 = white noise (averaging helps); near 1 = slow drift (averaging does not help, re-calibrate instead).")
    fig.suptitle(f"Noise PSD (mV^2/Hz) - {d['meta'].get('condition','')}"); save_fig(fig, a.out, "noise_psd.png")
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "noise_summary.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["channel", "mean_mV", "rms_clean_mV", "rms_all_mV", "mav_ac_mV", "pkpk_mV", "spikes_gt5sigma", "drift_mV_per_min", "lag1_autocorr", "share_40_50Hz", "snr_dB"]); w.writerows(rows)
    print("  wrote", os.path.join(a.out, "noise_summary.csv"))

# ------------------------------------------------------------------ filters
def causal(kind, fc, fs, x, despike):
    if despike:   # causal median of 3 (output at sample n uses n-2..n)
        y = x.copy(); y[2:] = np.median(np.stack([x[:-2], x[1:-1], x[2:]]), axis=0); y[:2] = x[:2]; x = y
    if kind == "none": return x
    if kind == "ma":
        N = max(1, round(0.443 * fs / fc)); k = np.ones(N) / N
        return np.convolve(np.r_[np.full(N - 1, x[0]), x], k, "valid")
    order = 2 if kind == "bw2" else 4
    sos = signal.butter(order, fc, fs=fs, output="sos")
    zi = signal.sosfilt_zi(sos) * x[0]
    return signal.sosfilt(sos, x, zi=zi)[0]

def delay_ms(kind, fc, fs, despike):
    if kind == "none": d = 0
    elif kind == "ma": d = (max(1, round(0.443 * fs / fc)) - 1) / 2 / fs * 1000
    else:
        sos = signal.butter(2 if kind == "bw2" else 4, fc, fs=fs, output="sos")
        w = np.linspace(0.01, 0.5, 200) * 2 * np.pi / 1; b, aa = signal.sos2tf(sos)
        _, gd = signal.group_delay((b, aa), w=[2 * np.pi * 0.2 / fs]); d = gd[0] / fs * 1000   # at 0.2 Hz
    return d + (1000 / fs if despike else 0)

def cmd_filters(a):
    mv = load_lab(a.csv); ch = chan(a.channel); fs = a.fs or round(mv["fs"])
    tg, x = resample_uniform(mv["t"], mv["raw"][:, ch], fs)
    if a.rest:
        rs = load_lab(a.rest); _, xr = resample_uniform(rs["t"], rs["raw"][:, ch], fs)
    else:   # no separate rest file: use the quietest 20 % of the movement file
        w = int(fs); sd = np.array([x[i:i + w].std() for i in range(0, len(x) - w, w // 2)])
        i0 = int(np.argmin(sd)) * (w // 2); xr = x[i0:i0 + 5 * w]
    # reference = zero-phase 4th order at 8 Hz: not causal (cannot be used live) but has no lag, so it marks where the movement really was
    ref = signal.sosfiltfilt(signal.butter(4, min(8, fs * .4), fs=fs, output="sos"), signal.medfilt(x, 5))   # median first so glitches are not smeared into the reference
    rows = []
    cands = [("none", 0, False), ("none", 0, True)] + [(k, fc, ds) for k in ("ma", "bw2", "bw4") for fc in (2, 3, 5, 8, 12) for ds in (False, True)]
    base_noise = xr.std()
    for kind, fc, ds in cands:
        y = causal(kind, fc or 1, fs, x, ds); yr = causal(kind, fc or 1, fs, xr, ds)
        lag = delay_ms(kind, fc or 1, fs, ds)
        # measured lag: shift that best aligns y with the reference
        best, bl = 0, 1e18
        for L in range(0, int(0.3 * fs)):
            e = np.mean((y[L:] - ref[:len(ref) - L]) ** 2)
            if e < bl: bl, best = e, L
        rows.append(dict(filter=kind, fc=fc, despike=ds, noise_rms=yr.std(), noise_red_dB=20 * np.log10(base_noise / max(yr.std(), 1e-9)),
                         lag_theory_ms=lag, lag_measured_ms=best * 1000 / fs, rmse_vs_ref=np.sqrt(bl)))
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, f"filters_{CH[ch]}.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    print(f"channel {CH[ch]}  fs {fs} Hz  rest noise (raw) {base_noise:.2f} mV rms")
    print(f"{'filter':<6}{'fc':>4} {'med3':<5}{'noise mV':>9}{'reduct dB':>10}{'lag th ms':>10}{'lag meas':>9}{'RMSE vs ref':>12}")
    for r in rows:
        print(f"{r['filter']:<6}{r['fc']:>4} {str(r['despike']):<5}{r['noise_rms']:9.2f}{r['noise_red_dB']:10.1f}{r['lag_theory_ms']:10.0f}{r['lag_measured_ms']:9.0f}{r['rmse_vs_ref']:12.2f}")
    ok = [r for r in rows if r["lag_theory_ms"] <= a.max_lag]
    if ok:
        b = min(ok, key=lambda r: r["rmse_vs_ref"])
        print(f"\nLowest error vs the zero-phase reference with lag <= {a.max_lag:.0f} ms: {b['filter']} fc={b['fc']} Hz median3={b['despike']}  (error {b['rmse_vs_ref']:.2f} mV rms, noise {b['noise_rms']:.2f} mV, lag {b['lag_theory_ms']:.0f} ms)")
        print("  (error = noise left + shape distortion; it already penalises both too little and too much smoothing. Confirm with real data: movement speed differs from the synthetic.)")
    fig, axs = plt.subplots(1, 3, figsize=(17, 4.8))
    for kind, mk in (("none", "k"), ("ma", "s"), ("bw2", "o"), ("bw4", "^")):
        rr = [r for r in rows if r["filter"] == kind and not r["despike"]]
        axs[0].plot([r["lag_theory_ms"] for r in rr], [r["noise_rms"] for r in rr], mk + "-", label=kind)
        for r in rr:
            if r["fc"]: axs[0].annotate(f"{r['fc']}", (r["lag_theory_ms"], r["noise_rms"]), fontsize=7, xytext=(3, 3), textcoords="offset points")
    axs[0].set_xlabel("lag (ms)"); axs[0].set_ylabel("residual noise at rest (mV rms)"); axs[0].set_title("Noise vs lag (labels = cut-off Hz)"); axs[0].legend(); axs[0].grid(alpha=.3)
    for kind, fc in (("bw2", 5), ("bw4", 5), ("ma", 5)):
        if kind == "ma":
            N = round(0.443 * fs / fc); w_, h = signal.freqz(np.ones(N) / N, 1, worN=2048, fs=fs)
        else:
            sos = signal.butter(2 if kind == "bw2" else 4, fc, fs=fs, output="sos"); w_, h = signal.sosfreqz(sos, worN=2048, fs=fs)
        axs[1].semilogx(w_[1:], 20 * np.log10(np.abs(h[1:]) + 1e-9), label=f"{kind} {fc} Hz")
    axs[1].axvline(50, color="r", ls=":", label="50 Hz (Nyquist)"); axs[1].set_ylim(-80, 3); axs[1].set_xlabel("Hz"); axs[1].set_ylabel("dB"); axs[1].set_title("Bode magnitude"); axs[1].legend(); axs[1].grid(alpha=.3, which="both")
    bw = causal("bw2", 5, fs, x, True); n = min(len(x), int(10 * fs))
    axs[2].plot(tg[:n] - tg[0], x[:n], lw=.6, alpha=.6, label="raw"); axs[2].plot(tg[:n] - tg[0], bw[:n], lw=1.4, label="bw2 5 Hz + med3"); axs[2].plot(tg[:n] - tg[0], ref[:n], "k--", lw=.8, label="zero-phase ref")
    axs[2].set_title("First 10 s"); axs[2].set_xlabel("s"); axs[2].set_ylabel("mV"); axs[2].legend(); axs[2].grid(alpha=.3)
    save_fig(fig, a.out, f"filters_{CH[ch]}.png")
    # time-domain features the brief names: MAV and RMS of raw vs filtered (windowed, 250 ms)
    w = int(.25 * fs); k = np.ones(w) / w
    for nm, sig_ in (("raw", x), ("bw2 5Hz+med3", bw)):
        ac = sig_ - np.convolve(sig_, k, "same")
        print(f"  {nm:<14} MAV(ac) {np.mean(np.abs(ac)):.2f} mV   RMS(ac) {np.sqrt(np.mean(ac**2)):.2f} mV")

# ------------------------------------------------------------------ FSR calibration
def cmd_fsrcal(a):
    """CSV columns: mass_g, mv   (repeat rows for repeated readings). Vcc and Rm from the command line."""
    m, v = [], []
    with open(a.csv) as f:
        for r in csv.DictReader(f): m.append(float(r["mass_g"])); v.append(float(r["mv"]))
    m, v = np.array(m), np.array(v); F = m * 9.80665 / 1000
    ok = (v > 5) & (v < a.vcc * .995); G = 1e6 / (a.rm * (a.vcc / v[ok] - 1))     # conductance in uS, same as the page's mvToN
    F = F[ok]
    k = float((G @ F) / (G @ G)); pred = k * G
    ss = ((F - pred) ** 2).sum(); r2 = 1 - ss / ((F - F.mean()) ** 2).sum()
    print(f"{ok.sum()} usable points of {len(v)}")
    print(f"force = k * G   k = {k:.5f} N per uS     R^2 = {r2:.3f}   rms error {np.sqrt(ss / len(F)):.2f} N ({100*np.sqrt(ss/len(F))/F.max():.0f} % of max)")
    A = np.polyfit(np.log(G), np.log(F), 1)
    print(f"power law check: F = {np.exp(A[1]):.4f} * G^{A[0]:.2f}  (exponent far from 1 -> linear k is a poor model; use a table or the power law)")
    print(f"Paste into Therapist settings: k = {k:.5f} (all fingers) - calibrate each FSR separately, they differ by 20-50 %.")
    print("FSR caveat: absolute accuracy is typically +-10-25 %, readings creep under constant load, and they depend on contact area. Use relative targets (as the page does).")
    fig, ax = plt.subplots(figsize=(5.5, 4.2)); ax.plot(G, F, "o"); gg = np.linspace(0, G.max(), 50)
    ax.plot(gg, k * gg, label=f"linear k={k:.4f}"); ax.plot(gg, np.exp(A[1]) * gg ** A[0], "--", label=f"power b={A[0]:.2f}")
    ax.set_xlabel("conductance (uS)"); ax.set_ylabel("force (N)"); ax.legend(); ax.grid(alpha=.3); save_fig(fig, a.out, "fsr_cal.png")

# ------------------------------------------------------------------ paired statistics
def cmd_stats(a):
    """CSV columns: participant, <a>, <b> [, order]   one row per participant, e.g. accuracy with raw vs filtered signal."""
    rows = list(csv.DictReader(open(a.csv)))
    A = np.array([float(r[a.a]) for r in rows]); B = np.array([float(r[a.b]) for r in rows]); n = len(A)
    dlt = B - A
    print(f"n = {n} pairs   {a.a}: mean {A.mean():.3f} (sd {A.std(ddof=1):.3f})   {a.b}: mean {B.mean():.3f} (sd {B.std(ddof=1):.3f})")
    if n < 3: sys.exit("need at least 3 pairs")
    t, p = stats.ttest_rel(B, A); dz = dlt.mean() / dlt.std(ddof=1)
    se = dlt.std(ddof=1) / np.sqrt(n); ci = stats.t.interval(.95, n - 1, loc=dlt.mean(), scale=se)
    print(f"mean difference ({a.b} - {a.a}) = {dlt.mean():.3f}   95 % CI [{ci[0]:.3f}, {ci[1]:.3f}]")
    print(f"paired t({n-1}) = {t:.2f}, p = {p:.4f}, Cohen's dz = {dz:.2f}")
    try:
        w, pw = stats.wilcoxon(dlt); print(f"Wilcoxon signed-rank W = {w:.1f}, p = {pw:.4f}")
    except ValueError as e: print("Wilcoxon not possible:", e)
    if n >= 3:
        sh = stats.shapiro(dlt); print(f"Shapiro-Wilk on the differences: W = {sh.statistic:.3f}, p = {sh.pvalue:.3f}  ({'normal enough' if sh.pvalue > .05 else 'not normal: trust Wilcoxon'})")
    if "order" in rows[0]:
        o = np.array([r["order"] for r in rows]); g = sorted(set(o))
        if len(g) == 2:
            d1, d2 = dlt[o == g[0]], dlt[o == g[1]]
            if len(d1) > 1 and len(d2) > 1:
                tt, pp = stats.ttest_ind(d1, d2, equal_var=False)
                print(f"Order effect (difference for order {g[0]} vs {g[1]}): Welch t = {tt:.2f}, p = {pp:.3f}  (counterbalance conditions, otherwise learning/fatigue is confounded with filtering)")
    print(f"Power note: with n = {n} only |dz| >= {stats.t.ppf(.975, n-1) / np.sqrt(n):.2f} is detectable at p<.05 with 50 % power. Report effect sizes and CIs; do not claim clinical benefit.")
    fig, ax = plt.subplots(figsize=(4.5, 4.2))
    for x_, y_ in zip(A, B): ax.plot([0, 1], [x_, y_], "o-", color="gray", alpha=.6)
    ax.plot([0, 1], [A.mean(), B.mean()], "ro-", lw=3); ax.set_xticks([0, 1]); ax.set_xticklabels([a.a, a.b]); ax.grid(alpha=.3); ax.set_title(f"paired, p={p:.3f}")
    save_fig(fig, a.out, "paired.png")

# ------------------------------------------------------------------ synthetic data
def write_lab(path, cond, t, raw, filt, pid="SYN"):
    hdr = ["# flexplay signal lab (SYNTHETIC)", f"# participant={pid}", f"# condition={cond}", "# start=2026-01-01T00:00:00Z", "# source=synthetic", "# fs_nominal_hz=100",
           "# filter=bw2 despike=true fc_flex=5 fc_tip=10 fc_force=5", "# rm_ohm=10000 vcc_mV=3300", "# device=none", "# lost_lines=0 corrupt_lines=0"]
    cols = ["t_s", "seq", "t_dev_ms"] + ["raw_" + k for k in CH] + ["filt_" + k for k in CH]
    with open(path, "w") as f:
        f.write("\n".join(hdr) + "\n" + ",".join(cols) + "\n")
        for i in range(len(t)):
            f.write(f"{t[i]:.4f},{i},{t[i]*1000:.1f}," + ",".join(f"{v:.1f}" for v in raw[i]) + "," + ",".join(f"{v:.3f}" for v in filt[i]) + "\n")

def cmd_synth(a):
    rng = np.random.default_rng(a.seed); fs = 100; os.makedirs(a.out, exist_ok=True)
    def make(dur, move):
        t = np.arange(0, dur, 1 / fs); n = len(t); raw = np.zeros((n, 10))
        for i in range(5):
            base = 1200 + 40 * i
            mv = (1300 * np.clip(.5 + .5 * np.sin(2 * np.pi * (0.4 + .05 * i) * t), 0, 1) ** 1.5) if move else 0
            x = base + mv + rng.normal(0, 3, n) + 0.5 * t / 60 * rng.choice([-1, 1])      # white noise + slow drift
            x += 6 * np.sin(2 * np.pi * 50 * t + rng.uniform(0, 6)) + 4 * np.sin(2 * np.pi * 100.4 * t)   # mains: 50 Hz at Nyquist, 100.4 folds to a 0.4 Hz beat
            sp = rng.random(n) < .002; x[sp] += rng.choice([-1, 1], sp.sum()) * rng.uniform(300, 900, sp.sum())   # contact glitches
            raw[:, i] = x
        raw[:, 5:9] = 0; raw[:, 9] = 0
        filt = np.column_stack([signal.sosfilt(signal.butter(2, 5, fs=fs, output="sos"), raw[:, i]) for i in range(10)])
        return t, raw, filt
    for nm, mv, dur in (("lab_still.csv", False, a.minutes * 60), ("lab_move.csv", True, a.minutes * 60)):
        t, r, f = make(dur, mv); write_lab(os.path.join(a.out, nm), "Still (noise floor)" if not mv else "Slow flexion", t, r, f); print("  wrote", os.path.join(a.out, nm))
    n = 12; true = rng.normal(0, 1, n); order = np.array(["RF", "FR"] * (n // 2))
    raw_acc = 70 + 4 * true + rng.normal(0, 3, n); fil_acc = raw_acc + 6 + rng.normal(0, 3, n)
    with open(os.path.join(a.out, "paired.csv"), "w") as f:
        f.write("participant,order,raw,filtered\n")
        for i in range(n): f.write(f"S{i+1:02d},{order[i]},{raw_acc[i]:.1f},{fil_acc[i]:.1f}\n")
    print("  wrote", os.path.join(a.out, "paired.csv"))
    with open(os.path.join(a.out, "fsr_weights.csv"), "w") as f:
        f.write("mass_g,mv\n")
        for m in (0, 100, 200, 500, 1000, 2000):
            for _ in range(3):
                Fn = m * 9.80665 / 1000; G = max(Fn / .03, 1e-6) ** 1.0 * (1 + rng.normal(0, .04)); R = 1e6 / max(G, 1e-6); v = 3300 * 10000 / (10000 + R) if m else 0
                f.write(f"{m},{v:.1f}\n")
    print("  wrote", os.path.join(a.out, "fsr_weights.csv"), "(true k = 0.03)")

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("synth"); s.add_argument("--out", default="demo"); s.add_argument("--minutes", type=float, default=5); s.add_argument("--seed", type=int, default=1); s.set_defaults(f=cmd_synth)
    s = sp.add_parser("noise"); s.add_argument("csv"); s.add_argument("--signal"); s.add_argument("--fs", type=int); s.add_argument("--out", default="out"); s.set_defaults(f=cmd_noise)
    s = sp.add_parser("filters"); s.add_argument("csv"); s.add_argument("--rest"); s.add_argument("--channel", default="flex_index"); s.add_argument("--fs", type=int); s.add_argument("--max-lag", type=float, default=100); s.add_argument("--out", default="out"); s.set_defaults(f=cmd_filters)
    s = sp.add_parser("fsrcal"); s.add_argument("csv"); s.add_argument("--rm", type=float, default=10000); s.add_argument("--vcc", type=float, default=3300); s.add_argument("--out", default="out"); s.set_defaults(f=cmd_fsrcal)
    s = sp.add_parser("stats"); s.add_argument("csv"); s.add_argument("--a", default="raw"); s.add_argument("--b", default="filtered"); s.add_argument("--out", default="out"); s.set_defaults(f=cmd_stats)
    a = ap.parse_args(); a.f(a)

if __name__ == "__main__":
    main()
