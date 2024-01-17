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

                if (e.Address == OutputState.Address) {
                    DigitalOutputState = OutputState.GetPayload(e);
                    UpdateDigitalOutputVisuals();
                }

                if (e.Address == AuxInState.Address) {
                    AuxiliaryInputState = AuxInState.GetPayload(e);
                    UpdateAuxiliaryInputVisuals();
                }
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
            // Source.DoCommand("test command");
        }

        private void lineButton0_Click(object sender, EventArgs e)
        {
            var commandMessage = DigitalOutputState.HasFlag(DigitalOutputs.Out0) 
                ? OutputClear.FromPayload(Bonsai.Harp.MessageType.Write, DigitalOutputs.Out0) 
                : OutputSet.FromPayload(Bonsai.Harp.MessageType.Write, DigitalOutputs.Out0);
            var readMessage = OutputState.FromPayload(Bonsai.Harp.MessageType.Read, DigitalOutputs.Out0);

            Source.DoCommand(commandMessage);
            Source.DoCommand(readMessage);
        }

        private void lineButton1_Click(object sender, EventArgs e)
        {
            var commandMessage = DigitalOutputState.HasFlag(DigitalOutputs.Out1) 
                ? OutputClear.FromPayload(Bonsai.Harp.MessageType.Write, DigitalOutputs.Out1) 
                : OutputSet.FromPayload(Bonsai.Harp.MessageType.Write, DigitalOutputs.Out1);
            var readMessage = OutputState.FromPayload(Bonsai.Harp.MessageType.Read, DigitalOutputs.Out1);

            Source.DoCommand(commandMessage);
            Source.DoCommand(readMessage);
        }

        private void lineButton2_Click(object sender, EventArgs e)
        {
            var commandMessage = DigitalOutputState.HasFlag(DigitalOutputs.Out2) 
                ? OutputClear.FromPayload(Bonsai.Harp.MessageType.Write, DigitalOutputs.Out2) 
                : OutputSet.FromPayload(Bonsai.Harp.MessageType.Write, DigitalOutputs.Out2);
            var readMessage = OutputState.FromPayload(Bonsai.Harp.MessageType.Read, DigitalOutputs.Out2);

            Source.DoCommand(commandMessage);
            Source.DoCommand(readMessage);
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
