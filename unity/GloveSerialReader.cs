// GloveSerialReader.cs  -  reads the FlexPlay glove (ESP32 firmware v2) in Unity and exposes calibrated, filtered values.
//
// UNTESTED IN THIS REPO: written without a Unity editor, so compile it once and fix typos before relying on it.
// Unity setup:  Edit > Project Settings > Player > Other Settings > Api Compatibility Level = ".NET Framework"
//               (System.IO.Ports does not exist in ".NET Standard 2.1").
// Line format:  seq,t_ms,v0..v9,flags*HH   HH = XOR of every character before '*'  (legacy "t_ms,v0..v9" is also accepted)
// Channels:     0-4 flex thumb..little (mV), 5-8 fingertip FSR (mV), 9 squeeze-ball FSR (mV)
using System;
using System.Collections.Concurrent;
using System.Globalization;
using System.IO.Ports;
using System.Threading;
using UnityEngine;

public class GloveSerialReader : MonoBehaviour
{
    public static GloveSerialReader Instance { get; private set; }

    [Header("Serial")]
    [Tooltip("Empty = try every port that looks like an ESP32 (COMx, /dev/ttyUSB*, /dev/ttyACM*, /dev/cu.usbserial*)")]
    public string portName = "";
    public int baud = 460800;

    [Header("Signal chain (same as the web page)")]
    public bool useFilter = true;               // false = raw values, for the raw-vs-processed comparison
    public bool despike = true;                 // median of 3
    [Range(0.5f, 20f)] public float cutoffHz = 5f;
    public int sampleRate = 100;
    public float signalTimeoutS = 0.35f;        // no good line for this long = SignalOk false

    [Header("Fallback")]
    [Tooltip("No glove? Space = fist, 1-5 = single finger. Lets you develop the game without hardware.")]
    public bool keyboardSimulator = true;

    // ---- outputs (read from the main thread) ----
    public float[] FlexRawFrac = new float[5];      // calibrated 0..1, unfiltered
    public float[] FlexFrac = new float[5];         // calibrated 0..1, filtered (if useFilter)
    public float FlexMean => Mean(useFilter ? FlexFrac : FlexRawFrac);
    public float FlexMeanRaw => Mean(FlexRawFrac);
    public float FlexMeanFiltered => Mean(FlexFrac);
    public float[] MvRaw = new float[10];
    public float[] MvFiltered = new float[10];
    public bool SignalOk { get; private set; }
    public bool Connected { get; private set; }
    public string Status { get; private set; } = "starting";
    public int Lines, Dropped, Corrupt;
    public int BoardFlags;                          // bits 0-2 = ADS1115 not responding, 3 = firmware overrun, 4 = ESP ADC range
    public float[] FlatMv = { 1200, 1200, 1200, 1200, 1200 };
    public float[] FistMv = { 2500, 2500, 2500, 2500, 2500 };

    // ---- internals ----
    public struct Sample { public int seq; public float[] v; public int flags; public bool legacy; }
    readonly ConcurrentQueue<Sample> queue = new ConcurrentQueue<Sample>();
    Thread thread; volatile bool run; volatile string threadStatus = "starting"; volatile bool threadConnected;
    Chain[] chains = new Chain[10];
    float lastGoodTime = -10f; int lastSeq = -1;
    float[] calAccum; int calCount; bool calActive; float[] calTarget;

    void Awake() { Instance = this; BuildChains(); }
    void OnEnable() { run = true; thread = new Thread(ReadLoop) { IsBackground = true, Name = "GloveSerial" }; thread.Start(); }
    void OnDisable() { run = false; if (thread != null && !thread.Join(1500)) thread.Abort(); }
    void OnValidate() { if (chains != null && chains[0] != null) BuildChains(); }

    void BuildChains() { for (int i = 0; i < 10; i++) chains[i] = new Chain(cutoffHz, sampleRate, despike); }

    // ---------------------------------------------------------------- calibration (call from your UI)
    public void CaptureFlat() { StartCal(FlatMv); }
    public void CaptureFist() { StartCal(FistMv); }
    void StartCal(float[] target) { calTarget = target; calAccum = new float[5]; calCount = 0; calActive = true; }
    /// Call after ~2-3 s of holding the pose. Returns false when too few samples arrived.
    public bool FinishCalibration()
    {
        calActive = false;
        if (calCount < 20) return false;
        for (int i = 0; i < 5; i++) calTarget[i] = calAccum[i] / calCount;
        return true;
    }

    // ---------------------------------------------------------------- main-thread update
    void Update()
    {
        Connected = threadConnected; Status = threadStatus;
        int got = 0; Sample s;
        while (queue.TryDequeue(out s))
        {
            got++;
            if (s.seq >= 0)
            {
                if (lastSeq >= 0)
                {
                    int d = (int)((uint)(s.seq - lastSeq));
                    if (d == 0) continue;
                    if (d > 1 && d < 1000) Dropped += d - 1;
                }
                lastSeq = s.seq;
            }
            Lines++; BoardFlags = s.flags;
            for (int i = 0; i < 10; i++) { MvRaw[i] = s.v[i]; MvFiltered[i] = chains[i].Step(s.v[i]); }
            if ((s.flags & 7) == 0) lastGoodTime = Time.unscaledTime;
            Apply();
            if (calActive) { for (int i = 0; i < 5; i++) calAccum[i] += MvRaw[i]; calCount++; }
        }
        if (got == 0 && !Connected && keyboardSimulator) { Simulate(); return; }
        SignalOk = (Time.unscaledTime - lastGoodTime) < signalTimeoutS;
    }

    void Apply()
    {
        for (int i = 0; i < 5; i++)
        {
            float span = FistMv[i] - FlatMv[i]; if (Mathf.Abs(span) < 1f) span = 1f;
            FlexRawFrac[i] = Mathf.Clamp((MvRaw[i] - FlatMv[i]) / span, 0f, 1.4f);
            FlexFrac[i] = Mathf.Clamp((MvFiltered[i] - FlatMv[i]) / span, 0f, 1.4f);
        }
    }

    // keyboard stand-in so the game can be developed with no glove at all
    float simFist;
    void Simulate()
    {
        float target = Input.GetKey(KeyCode.Space) ? 1f : 0f;
        simFist = Mathf.MoveTowards(simFist, target, Time.unscaledDeltaTime * 2.5f);
        for (int i = 0; i < 5; i++)
        {
            float f = simFist;
            if (Input.GetKey(KeyCode.Alpha1 + i)) f = 1f;
            float noisy = FlatMv[i] + f * (FistMv[i] - FlatMv[i]) + UnityEngine.Random.Range(-6f, 6f);
            MvRaw[i] = noisy; MvFiltered[i] = chains[i].Step(noisy);
        }
        Apply(); SignalOk = true; lastGoodTime = Time.unscaledTime; Status = "keyboard simulator";
    }

    static float Mean(float[] a) { float s = 0; for (int i = 0; i < a.Length; i++) s += a[i]; return s / a.Length; }

    // ---------------------------------------------------------------- reader thread (never touches Unity objects)
    void ReadLoop()
    {
        while (run)
        {
            SerialPort sp = null;
            try
            {
                string name = portName;
                if (string.IsNullOrEmpty(name)) name = FindPort();
                if (string.IsNullOrEmpty(name)) { threadStatus = "no serial port found"; threadConnected = false; Sleep(1000); continue; }
                sp = new SerialPort(name, baud, Parity.None, 8, StopBits.One) { ReadTimeout = 200, NewLine = "\n", DtrEnable = false, RtsEnable = false, ReadBufferSize = 1 << 16 };
                sp.Open(); threadStatus = "connected " + name; threadConnected = true;
                var sb = new System.Text.StringBuilder();
                var buf = new byte[4096];
                int idleMs = 0;
                while (run)
                {
                    int n;
                    try { n = sp.Read(buf, 0, buf.Length); }
                    catch (TimeoutException) { idleMs += 200; if (idleMs > 1500) { threadStatus = "no data (wrong baud rate?)"; } continue; }
                    idleMs = 0; threadStatus = "connected " + name;
                    for (int i = 0; i < n; i++)
                    {
                        char c = (char)buf[i];
                        if (c == '\n') { Handle(sb.ToString().Trim()); sb.Length = 0; }
                        else if (c != '\r') { sb.Append(c); if (sb.Length > 512) sb.Length = 0; }   // garbage without newline must not grow forever
                    }
                }
            }
            catch (Exception e) { threadStatus = "serial error: " + e.Message; }
            finally
            {
                threadConnected = false;
                try { if (sp != null && sp.IsOpen) sp.Close(); } catch { }
            }
            Sleep(1000);      // unplugged or failed: retry, so re-plugging the cable needs no restart
        }
    }

    static void Sleep(int ms) { Thread.Sleep(ms); }

    static string FindPort()
    {
        foreach (var p in SerialPort.GetPortNames())
            if (p.StartsWith("COM") || p.Contains("ttyUSB") || p.Contains("ttyACM") || p.Contains("usbserial") || p.Contains("usbmodem")) return p;
        return null;
    }

    void Handle(string line)
    {
        if (line.Length == 0 || line[0] == '#') return;
        Sample s;
        if (!Parse(line, out s)) { Interlocked.Increment(ref Corrupt); return; }
        queue.Enqueue(s);
    }

    public static bool Parse(string line, out Sample s)
    {
        s = default(Sample);
        int star = line.LastIndexOf('*'); string body = line; bool legacy = true;
        if (star >= 0)
        {
            string cs = line.Substring(star + 1).Trim();
            int want;
            if (cs.Length != 2 || !int.TryParse(cs, NumberStyles.HexNumber, CultureInfo.InvariantCulture, out want)) return false;
            body = line.Substring(0, star);
            int x = 0; for (int i = 0; i < body.Length; i++) x ^= body[i];
            if (x != want) return false;
            legacy = false;
        }
        string[] f = body.Split(',');
        int vi = legacy ? 1 : 2;
        if (legacy ? f.Length != 11 : f.Length != 13) return false;
        var v = new float[10];
        for (int i = 0; i < 10; i++)
        {
            float val;
            if (!float.TryParse(f[vi + i], NumberStyles.Float, CultureInfo.InvariantCulture, out val) || float.IsNaN(val) || val < -500f || val > 5500f) return false;
            v[i] = val;
        }
        int seq = -1, flags = 0;
        if (!legacy)
        {
            if (!int.TryParse(f[0], NumberStyles.Integer, CultureInfo.InvariantCulture, out seq)) return false;
            if (!int.TryParse(f[12], NumberStyles.Integer, CultureInfo.InvariantCulture, out flags)) return false;
        }
        s = new Sample { seq = seq, v = v, flags = flags, legacy = legacy };
        return true;
    }

    // ---------------------------------------------------------------- causal 2nd-order Butterworth + median-of-3 (same maths as the web page)
    class Chain
    {
        readonly bool ds; readonly float b0, b1, b2, a1, a2;
        float x1, x2, y1, y2; bool init; float m0, m1, m2; bool mInit;
        public Chain(float fc, int fs, bool despike)
        {
            ds = despike; fc = Mathf.Clamp(fc, 0.2f, fs * 0.45f);
            double w = 2.0 * Math.PI * fc / fs, c = Math.Cos(w), al = Math.Sin(w) / (2.0 * Math.Sqrt(0.5)), a0 = 1.0 + al;
            b0 = (float)((1 - c) / 2 / a0); b1 = (float)((1 - c) / a0); b2 = b0; a1 = (float)(-2 * c / a0); a2 = (float)((1 - al) / a0);
        }
        public float Step(float x)
        {
            if (ds)
            {
                if (!mInit) { m0 = m1 = m2 = x; mInit = true; } else { m0 = m1; m1 = m2; m2 = x; }
                x = Mathf.Max(Mathf.Min(m0, m1), Mathf.Min(Mathf.Max(m0, m1), m2));
            }
            if (!init) { x1 = x2 = y1 = y2 = x; init = true; }          // start at steady state: no start-up transient
            float y = b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2;
            x2 = x1; x1 = x; y2 = y1; y1 = y; return y;
        }
    }
}
