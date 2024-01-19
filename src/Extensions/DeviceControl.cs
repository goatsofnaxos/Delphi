using Bonsai;
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Data;
using System.Drawing;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Forms;
using Harp.OutputExpander;

namespace Extensions.Extensions
{
    public partial class DeviceControl : UserControl
    {
        DeviceVisualInterface Source;

        // TODO - this sort of thing should be in separate state class with some INotifyPropertyChanged interface
        DigitalOutputs DigitalOutputState = DigitalOutputs.None;
        AuxiliaryInputs AuxiliaryInputState = AuxiliaryInputs.None;

        public DeviceControl(DeviceVisualInterface source)
        {
            Source = source;
            InitializeComponent();

            source.OnReceiveHarpMessage += (sender, e) => {
                testLabel.Text = e.ToString();

                // Detect changes to output state
                if (e.Address == OutputState.Address) {
                    DigitalOutputState = OutputState.GetPayload(e);
                    UpdateDigitalOutputVisuals();
                }

                // If an output was set, read the current output state
                if (e.Address == OutputSet.Address || e.Address == OutputClear.Address)
                {
                    Source.DoCommand(OutputState.FromPayload(Bonsai.Harp.MessageType.Read, DigitalOutputs.Out0));
                }

                if (e.Address == AuxInState.Address) {
                    AuxiliaryInputState = AuxInState.GetPayload(e);
                    UpdateAuxiliaryInputVisuals();
                }
            };

            // Detect session rule change
            source.OnReceiveRuleChange += (sender, e) =>
            {
                currentRuleLabel.Text = e;
            };

            // Detect state change
            source.OnReceiveStateChange += (sender, e) =>
            {
                currentStateLabel.Text = e;
            };

            // Detect poke count change
            source.OnReceivePokeCountChange += (sender, e) =>
            {
                pokeCountLabel.Text = e.ToString();
            };
        }

        // TODO - horrible, there's a better way to do this
        private void UpdateDigitalOutputVisuals() {
            lineButton0.BackColor = DigitalOutputState.HasFlag(DigitalOutputs.Out0) ? Color.White : Color.Gray;
            lineButton1.BackColor = DigitalOutputState.HasFlag(DigitalOutputs.Out1) ? Color.White : Color.Gray;
            lineButton2.BackColor = DigitalOutputState.HasFlag(DigitalOutputs.Out2) ? Color.White : Color.Gray;
            lineButton3.BackColor = DigitalOutputState.HasFlag(DigitalOutputs.Out3) ? Color.White : Color.Gray;
            lineButton4.BackColor = DigitalOutputState.HasFlag(DigitalOutputs.Out4) ? Color.White : Color.Gray;
            lineButton5.BackColor = DigitalOutputState.HasFlag(DigitalOutputs.Out5) ? Color.White : Color.Gray;
            lineButton6.BackColor = DigitalOutputState.HasFlag(DigitalOutputs.Out6) ? Color.White : Color.Gray;
            lineButton7.BackColor = DigitalOutputState.HasFlag(DigitalOutputs.Out7) ? Color.White : Color.Gray;
            lineButton8.BackColor = DigitalOutputState.HasFlag(DigitalOutputs.Out8) ? Color.White : Color.Gray;
            lineButton9.BackColor = DigitalOutputState.HasFlag(DigitalOutputs.Out9) ? Color.White : Color.Gray;
        }

        private void UpdateAuxiliaryInputVisuals() {
            auxInState0.Checked = AuxiliaryInputState.HasFlag(AuxiliaryInputs.Aux0);
            auxInState1.Checked = AuxiliaryInputState.HasFlag(AuxiliaryInputs.Aux1);
        }

        private void DeviceControl_Load(object sender, EventArgs e)
        {

        }

        private void testLabel_Click(object sender, EventArgs e)
        {

        }

        private void testButton_Click(object sender, EventArgs e)
        {

        }

        void ToggleLine(DigitalOutputs line)
        {
            var commandMessage = DigitalOutputState.HasFlag(line)
                ? OutputClear.FromPayload(Bonsai.Harp.MessageType.Write, line)
                : OutputSet.FromPayload(Bonsai.Harp.MessageType.Write, line);

            Source.DoCommand(commandMessage);
        }


        private void lineButton0_Click(object sender, EventArgs e)
        {
            ToggleLine(DigitalOutputs.Out0);
        }

        private void lineButton1_Click(object sender, EventArgs e)
        {
            ToggleLine(DigitalOutputs.Out1);
        }

        private void lineButton2_Click(object sender, EventArgs e)
        {
            ToggleLine(DigitalOutputs.Out2);
        }

        private void lineButton3_Click(object sender, EventArgs e)
        {
            ToggleLine(DigitalOutputs.Out3);
        }

        private void lineButton4_Click(object sender, EventArgs e)
        {
            ToggleLine(DigitalOutputs.Out4);
        }

        private void lineButton5_Click(object sender, EventArgs e)
        {
            ToggleLine(DigitalOutputs.Out5);
        }

        private void lineButton6_Click(object sender, EventArgs e)
        {
            ToggleLine(DigitalOutputs.Out6);
        }

        private void lineButton7_Click(object sender, EventArgs e)
        {
            ToggleLine(DigitalOutputs.Out7);
        }

        private void lineButton8_Click(object sender, EventArgs e)
        {
            ToggleLine(DigitalOutputs.Out8);
        }

        private void lineButton9_Click(object sender, EventArgs e)
        {
            ToggleLine(DigitalOutputs.Out9);
        }

        private void auxInState1_CheckedChanged(object sender, EventArgs e)
        {

        }

        private void auxInState2_CheckedChanged(object sender, EventArgs e)
        {

        }

        private void setAuxInput0Button_Click(object sender, EventArgs e)
        {

        }

        private void setAuxInput1Button_Click(object sender, EventArgs e)
        {

        }
    }
}
