// using Bonsai;
// using System;
// using System.ComponentModel;
// using System.Linq;
// using System.Reactive.Linq;
// using SpinnakerNET;
// using Bonsai.Harp;

// namespace DragonflySpinnakerCapture
// {
//     [Description("Configures and initializes a dragonfly Spinnaker camera for triggered acquisition.")]
//     public class DragonflySpinnakerCapture: Bonsai.Spinnaker.SpinnakerCapture
//     {
//         public DragonflySpinnakerCapture()
//         {
//             ExposureTime = 1e6 / 50 - 1000;
//             Binning = 1;
//         }

//         [Description("The duration of each individual exposure, in microseconds. In general, this should be 1 / frameRate - 1 millisecond to prepare for next trigger.")]
//         public double ExposureTime { get; set; }

//         [Description("The gain of the sensor.")]
//         public double Gain { get; set; }

//         [Description("The size of the binning area of the sensor, e.g. a binning size of 2 specifies a 2x2 binning region.")]
//         public int Binning { get; set; }

//         protected override void Configure(IManagedCamera camera)
//         {
//             try { camera.AcquisitionStop.Execute(); }
//             catch { }
//             camera.BinningSelector.Value = BinningSelectorEnums.All.ToString();
//             camera.BinningHorizontalMode.Value = BinningHorizontalModeEnums.Sum.ToString();
//             camera.BinningVerticalMode.Value = BinningVerticalModeEnums.Sum.ToString();
//             camera.BinningHorizontal.Value = Binning;
//             camera.BinningVertical.Value = Binning;
//             camera.AcquisitionFrameRateEnable.Value = false;
//             camera.TriggerMode.Value = TriggerModeEnums.On.ToString();
//             camera.TriggerSelector.Value = TriggerSelectorEnums.FrameStart.ToString();
//             camera.TriggerSource.Value = TriggerSourceEnums.Line0.ToString();
//             camera.TriggerOverlap.Value = TriggerOverlapEnums.Off.ToString();
//             camera.TriggerActivation.Value = TriggerActivationEnums.RisingEdge.ToString();
//             camera.ExposureAuto.Value = ExposureAutoEnums.Off.ToString();
//             camera.ExposureMode.Value = ExposureModeEnums.Timed.ToString();
//             camera.ExposureTime.Value = ExposureTime;
//             camera.DeviceLinkThroughputLimit.Value = camera.DeviceLinkThroughputLimit.Max;
//             camera.GainAuto.Value = GainAutoEnums.Off.ToString();
//             camera.Gain.Value = Gain;
//             base.Configure(camera);
//         }
//     }
// }


using Bonsai;
using System;
using System.ComponentModel;
using System.Linq;
using System.Reactive.Linq;
using SpinnakerNET;
using Bonsai.Harp;

namespace DragonflySpinnakerCapture
{
    [Description("Configures and initializes a dragonfly Spinnaker camera for triggered acquisition.")]
    public class DragonflySpinnakerCapture : Bonsai.Spinnaker.SpinnakerCapture
    {
        public DragonflySpinnakerCapture()
        {
            ExposureTime = 1e6 / 50 - 1000;
            Binning = 1;
            UseGlobalReset = true;
            PixelFormat = PixelFormatEnums.Mono8;
            AdcBitDepth = AdcBitDepthEnums.Bit10;
            TriggerDelay = 5000;
        }

        [Description("The duration of each individual exposure, in microseconds. In general, this should be 1 / frameRate - 1 millisecond to prepare for next trigger.")]
        public double ExposureTime { get; set; }

        [Description("The gain of the sensor.")]
        public double Gain { get; set; }

        [Description("The size of the binning area of the sensor, e.g. a binning size of 2 specifies a 2x2 binning region.")]
        public int Binning { get; set; }

        [Description("If true, the sensor's rolling shutter is switched into Global Reset (GRR) mode: all rows start integrating simultaneously, approximating a global shutter for the START of exposure. Rows are still read out sequentially, so for this to look like a true global-shutter frame (no top-to-bottom brightness gradient), the scene must be lit with a strobe fired only during the exposure overlap window, or must not be moving/changing during exposure. With continuous ambient lighting this will NOT fully match Blackfly-style global shutter behavior.")]
        public bool UseGlobalReset { get; set; }

        [Description("Width of the region of interest, in pixels after binning. Set to null (default) to use the full sensor width.")]
        public int? Width { get; set; }

        [Description("Height of the region of interest, in pixels after binning. Set to null (default) to use the full sensor height.")]
        public int? Height { get; set; }

        [Description("Horizontal offset of the region of interest from the left edge of the sensor, in pixels after binning.")]
        public int OffsetX { get; set; }

        [Description("Vertical offset of the region of interest from the top edge of the sensor, in pixels after binning.")]
        public int OffsetY { get; set; }

        [Description("Delay of the frame being captured from when a triggered was received.")]
        public int TriggerDelay { get; set; }

        [Description("The pixel format used for image acquisition. Mono8 halves per-pixel bandwidth versus Mono16, which raises the achievable frame rate / reduces readout time for a given ROI and DeviceLinkThroughputLimit.")]
        public PixelFormatEnums PixelFormat { get; set; }

        [Description("The sensor's ADC bit depth. This is the setting that actually controls row-scan speed on a rolling shutter sensor (fewer bits per row = fewer ADC clock cycles per row = faster sweep from first row to last row, reducing both rolling-shutter skew and per-frame readout time). This is independent of PixelFormat: dropping PixelFormat to Mono8 without also lowering AdcBitDepth may only truncate an already-slow 10/12-bit conversion rather than speed up the sensor readout. Verify against SpinView which values are available on your unit (this camera's datasheet lists 10/12-bit only, no 8-bit ADC mode) before relying on this in an unattended pipeline.")]
        public AdcBitDepthEnums AdcBitDepth { get; set; }

        protected override void Configure(IManagedCamera camera)
        {
            try { camera.AcquisitionStop.Execute(); }
            catch { }

            // Trigger mode must be Off while selector/source/overlap/activation and
            // shutter mode are (re)configured; some of these are scoped per-selector
            // and are rejected or silently ignored while triggering is active.
            camera.TriggerMode.Value = TriggerModeEnums.Off.ToString();

            // --- Pixel format ---
            // AdcBitDepth is set first: it's the setting that actually changes the
            // sensor's row-scan speed, and it can also constrain which PixelFormat
            // values are valid, so set it before PixelFormat/Binning/ROI.
            camera.AdcBitDepth.Value = AdcBitDepth.ToString();
            camera.PixelFormat.Value = PixelFormat.ToString();

            // --- Binning ---
            camera.BinningSelector.Value = BinningSelectorEnums.All.ToString();
            camera.BinningHorizontalMode.Value = BinningHorizontalModeEnums.Sum.ToString();
            camera.BinningVerticalMode.Value = BinningVerticalModeEnums.Sum.ToString();
            camera.BinningHorizontal.Value = Binning;
            camera.BinningVertical.Value = Binning;

            // --- ROI ---
            // Zero the offsets first so a shrinking width/height never conflicts with
            // a previously configured offset (GenICam will reject Width/Height changes
            // that would push OffsetX/OffsetY + Width/Height past the sensor bounds).
            camera.OffsetX.Value = 0;
            camera.OffsetY.Value = 0;
            camera.Width.Value = Width ?? camera.Width.Max;
            camera.Height.Value = Height ?? camera.Height.Max;
            camera.OffsetX.Value = OffsetX;
            camera.OffsetY.Value = OffsetY;

            // --- Exposure / gain ---
            camera.AcquisitionFrameRateEnable.Value = false;
            camera.ExposureAuto.Value = ExposureAutoEnums.Off.ToString();
            camera.ExposureMode.Value = ExposureModeEnums.Timed.ToString();
            camera.ExposureTime.Value = ExposureTime;
            camera.GainAuto.Value = GainAutoEnums.Off.ToString();
            camera.Gain.Value = Gain;
            camera.TriggerDelay.Value = TriggerDelay;

            // --- Shutter mode ---
            // Rolling (default sensor behavior) vs GlobalReset (simultaneous row start).
            // NOTE: verify the exact enum type name against your installed SpinnakerNET
            // version — this follows the same pattern as the other *Enums types above,
            // but if SensorShutterModeEnums isn't present in your SDK version, set the
            // node via the generic string/node-map API instead, e.g.:
            //   camera.SensorShutterMode.Value = "GlobalReset";
            camera.SensorShutterMode.Value = (UseGlobalReset
                ? SensorShutterModeEnums.GlobalReset
                : SensorShutterModeEnums.Rolling).ToString();

            // --- Trigger configuration ---
            camera.TriggerSelector.Value = TriggerSelectorEnums.FrameStart.ToString();
            camera.TriggerSource.Value = TriggerSourceEnums.Line0.ToString();
            camera.TriggerActivation.Value = TriggerActivationEnums.RisingEdge.ToString();
            // With GlobalReset, prefer letting the next trigger be accepted during
            // readout of the current frame (ReadOut) rather than blocking until fully
            // done (Off). Not all camera models/firmware expose ReadOut for
            // TriggerOverlap (the Dragonfly S line, for one, may reject it with a
            // GenICam AccessException: "Enum entry is not writable"), so fall back to
            // Off if the camera refuses it.
            if (UseGlobalReset)
            {
                try
                {
                    camera.TriggerOverlap.Value = TriggerOverlapEnums.ReadOut.ToString();
                }
                catch (Exception)
                {
                    camera.TriggerOverlap.Value = TriggerOverlapEnums.Off.ToString();
                }
            }
            else
            {
                camera.TriggerOverlap.Value = TriggerOverlapEnums.Off.ToString();
            }

            camera.DeviceLinkThroughputLimit.Value = camera.DeviceLinkThroughputLimit.Max;

            // Hardware triggering is always used by this capture node.
            camera.TriggerMode.Value = TriggerModeEnums.On.ToString();

            base.Configure(camera);
        }
    }
}

