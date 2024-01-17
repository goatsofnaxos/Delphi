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

        public DeviceControl(DeviceVisualInterface source)
        {
            Source = source;
            InitializeComponent();

            source.OnReceiveHarpMessage += (sender, e) => {
                testLabel.Text = e.ToString();

                if (e.Address == OutputState.Address) {
                    DigitalOutputState = OutputState.GetPayload(e);
                }
            };
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
    }
}
