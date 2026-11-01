// PinchBallGame.cs  -  minimal glove-controlled game for the SSRP brief: raw vs processed comparison.
//
// The ball height follows the hand-closure signal (0 = open, 1 = fist). A target band jumps between two heights every few
// seconds; the player moves the ball into it and holds. For every jump we log the response time (jump -> ball inside the band
// for 0.25 s) and the tracking error. Blocks alternate between RAW and FILTERED input so the two can be compared on the same
// player ("movement accuracy" and "response time" in the brief).
//
// Setup: empty scene -> create an empty GameObject -> add GloveSerialReader and PinchBallGame. Press Play.
// Keys: F = switch raw/filtered by hand, C = calibrate (flat 2 s, then fist 3 s), Esc = end block and save, Space = fist in the simulator.
// VR: add XR Plug-in Management (OpenXR) + XR Origin, make this object's camera the XR camera and the HUD already is world-space.
// UNTESTED IN THIS REPO: no Unity editor was available. Compile it and fix typos first.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using UnityEngine;

public class PinchBallGame : MonoBehaviour
{
    public int trialsPerBlock = 10;
    public float trialSeconds = 5f;
    public float bandHalfWidth = 0.10f;           // fraction of range
    public float holdSeconds = 0.25f;
    public bool alternateBlocks = true;           // RAW, FILTERED, RAW, FILTERED ...
    public bool startWithRaw = true;              // counterbalance across participants
    public string participantId = "P01";

    GloveSerialReader glove; GameObject ball, band; TextMesh hud, banner; Material mat;
    float[] levels = { 0.15f, 0.8f };
    int trial, blockIndex; bool filteredBlock; float trialT0, inBandSince = -1f, responseTime = -1f; int targetIdx;
    float errSum; int errN; bool running = true;
    float nextBandJump;
    readonly StringBuilder frames = new StringBuilder(), trials = new StringBuilder();
    string stamp; float t0;
    // calibration state machine
    int calStage; float calT;

    void Start()
    {
        glove = GloveSerialReader.Instance != null ? GloveSerialReader.Instance : FindObjectOfType<GloveSerialReader>();
        if (glove == null) glove = gameObject.AddComponent<GloveSerialReader>();
        mat = new Material(Shader.Find("Sprites/Default"));
        var cam = Camera.main;
        if (cam == null) { var g = new GameObject("Main Camera"); g.tag = "MainCamera"; cam = g.AddComponent<Camera>(); }
        cam.orthographic = true; cam.orthographicSize = 3f; cam.transform.position = new Vector3(0, 0, -10); cam.backgroundColor = new Color(.05f, .08f, .14f); cam.clearFlags = CameraClearFlags.SolidColor;
        ball = Prim(PrimitiveType.Sphere, new Color(1f, .75f, .2f), new Vector3(.5f, .5f, .5f));
        band = Prim(PrimitiveType.Cube, new Color(.3f, .8f, .5f, .55f), new Vector3(3f, 1f, .1f));
        hud = MakeText("hud", new Vector3(-4.6f, 2.8f, 0), .09f, TextAnchor.UpperLeft);
        banner = MakeText("banner", new Vector3(0, 2.6f, 0), .14f, TextAnchor.UpperCenter);
        stamp = DateTime.Now.ToString("yyyy-MM-dd_HH-mm-ss"); t0 = Time.unscaledTime;
        frames.AppendLine("t_s,block,mode,trial,target,raw,filtered,used,error,signal_ok");
        trials.AppendLine("participant,block,mode,trial,target,response_time_s,reached,mean_abs_error_after_reach,mean_abs_error_whole_trial,signal_ok_all_trial");
        filteredBlock = !startWithRaw; glove.useFilter = filteredBlock;
        StartTrial();
    }

    GameObject Prim(PrimitiveType t, Color c, Vector3 scale)
    {
        var g = GameObject.CreatePrimitive(t); Destroy(g.GetComponent<Collider>());
        g.transform.localScale = scale; var r = g.GetComponent<Renderer>(); r.material = new Material(mat) { color = c }; return g;
    }

    TextMesh MakeText(string name, Vector3 pos, float size, TextAnchor anchor)
    {
        var g = new GameObject(name); g.transform.position = pos;
        var tm = g.AddComponent<TextMesh>(); tm.characterSize = size; tm.fontSize = 64; tm.anchor = anchor; tm.color = Color.white;
        Font f = null;
        try { f = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf"); } catch { }
        if (f == null) { try { f = Resources.GetBuiltinResource<Font>("Arial.ttf"); } catch { } }
        if (f != null) { tm.font = f; g.GetComponent<MeshRenderer>().material = f.material; }
        return tm;
    }

    float Y(float v) { return Mathf.Lerp(-2.2f, 2.2f, Mathf.Clamp01(v)); }

    void StartTrial()
    {
        targetIdx = (trial % 2 == 0) ? 1 : 0;                       // alternate high / low so every trial needs a real movement
        trialT0 = Time.unscaledTime; inBandSince = -1f; responseTime = -1f; errSum = 0; errN = 0; allSignalOk = true; reachedErrSum = 0; reachedErrN = 0;
    }
    bool allSignalOk; float reachedErrSum; int reachedErrN;

    void Update()
    {
        if (Input.GetKeyDown(KeyCode.F)) { filteredBlock = !filteredBlock; glove.useFilter = filteredBlock; }
        if (Input.GetKeyDown(KeyCode.C) && calStage == 0) { calStage = 1; calT = Time.unscaledTime; glove.CaptureFlat(); }
        if (Input.GetKeyDown(KeyCode.Escape)) { Finish(); return; }
        if (calStage > 0) { Calibrate(); return; }
        if (!running) return;

        float raw = glove.FlexMeanRaw, fil = glove.FlexMeanFiltered, used = glove.useFilter ? fil : raw;
        float target = levels[targetIdx];
        bool ok = glove.SignalOk;
        ball.transform.position = new Vector3(0, Y(used), 0);
        band.transform.position = new Vector3(0, Y(target), 0.5f);
        band.transform.localScale = new Vector3(3f, Mathf.Abs(Y(1) - Y(0)) * bandHalfWidth * 2f, .1f);

        if (!ok)
        {   // glove lost: freeze the trial clock (same rule as the web game) so a cable glitch never counts against the player
            trialT0 += Time.unscaledDeltaTime; allSignalOk = false;
            banner.text = "GLOVE SIGNAL LOST - " + glove.Status; return;
        }
        banner.text = "";
        float err = Mathf.Abs(used - target); float tt = Time.unscaledTime - trialT0;
        errSum += err; errN++;
        if (err <= bandHalfWidth)
        {
            if (inBandSince < 0) inBandSince = Time.unscaledTime;
            if (responseTime < 0 && Time.unscaledTime - inBandSince >= holdSeconds) responseTime = tt - holdSeconds;
        }
        else inBandSince = -1f;
        if (responseTime >= 0) { reachedErrSum += err; reachedErrN++; }

        frames.Append((Time.unscaledTime - t0).ToString("F3", CultureInfo.InvariantCulture)).Append(',').Append(blockIndex).Append(',').Append(glove.useFilter ? "filtered" : "raw").Append(',')
              .Append(trial).Append(',').Append(target.ToString("F2", CultureInfo.InvariantCulture)).Append(',').Append(raw.ToString("F4", CultureInfo.InvariantCulture)).Append(',')
              .Append(fil.ToString("F4", CultureInfo.InvariantCulture)).Append(',').Append(used.ToString("F4", CultureInfo.InvariantCulture)).Append(',').Append(err.ToString("F4", CultureInfo.InvariantCulture)).Append(',').Append(ok ? 1 : 0).AppendLine();

        hud.text = string.Format("Participant {0}\nMode: {1}   block {2}   trial {3}/{4}\nGlove: {5}  lines {6}  lost {7}  corrupt {8}  flags {9}\nF = raw/filtered   C = calibrate   Esc = save",
            participantId, glove.useFilter ? "FILTERED" : "RAW", blockIndex + 1, trial % trialsPerBlock + 1, trialsPerBlock, glove.Status, glove.Lines, glove.Dropped, glove.Corrupt, glove.BoardFlags);

        if (tt >= trialSeconds) EndTrial();
    }

    void EndTrial()
    {
        float meanAll = errN > 0 ? errSum / errN : float.NaN;
        float meanReached = reachedErrN > 0 ? reachedErrSum / reachedErrN : float.NaN;
        trials.Append(participantId).Append(',').Append(blockIndex).Append(',').Append(glove.useFilter ? "filtered" : "raw").Append(',').Append(trial).Append(',')
              .Append(levels[targetIdx].ToString("F2", CultureInfo.InvariantCulture)).Append(',').Append(responseTime >= 0 ? responseTime.ToString("F3", CultureInfo.InvariantCulture) : "").Append(',')
              .Append(responseTime >= 0 ? 1 : 0).Append(',').Append(F(meanReached)).Append(',').Append(F(meanAll)).Append(',').Append(allSignalOk ? 1 : 0).AppendLine();
        trial++;
        if (trial % trialsPerBlock == 0)
        {
            blockIndex++;
            if (alternateBlocks) { filteredBlock = !filteredBlock; glove.useFilter = filteredBlock; }
            if (blockIndex >= 4) { Finish(); return; }              // 2 raw + 2 filtered blocks
        }
        StartTrial();
    }

    static string F(float v) { return float.IsNaN(v) ? "" : v.ToString("F4", CultureInfo.InvariantCulture); }

    void Calibrate()
    {
        float e = Time.unscaledTime - calT;
        if (calStage == 1) { banner.text = "Hand flat and relaxed: " + Mathf.Max(0, 2f - e).ToString("F0") + " s"; if (e >= 2f) { glove.FinishCalibration(); calStage = 2; calT = Time.unscaledTime; glove.CaptureFist(); } }
        else if (calStage == 2) { banner.text = "Make a comfortable fist and hold: " + Mathf.Max(0, 3f - e).ToString("F0") + " s"; if (e >= 3f) { bool ok = glove.FinishCalibration(); calStage = 0; banner.text = ok ? "Calibrated" : "Calibration failed: too few samples"; } }
    }

    void Finish()
    {
        if (!running) return; running = false;
        string dir = Application.persistentDataPath;
        try
        {
            File.WriteAllText(Path.Combine(dir, "pinchball_frames_" + participantId + "_" + stamp + ".csv"), frames.ToString());
            File.WriteAllText(Path.Combine(dir, "pinchball_trials_" + participantId + "_" + stamp + ".csv"), trials.ToString());
            banner.text = "Saved to " + dir;
        }
        catch (Exception ex) { banner.text = "Could not save: " + ex.Message; }
        Debug.Log("PinchBallGame saved to " + dir);
    }

    void OnApplicationQuit() { Finish(); }
}
