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

namespace Extensions.Extensions
{
    public partial class DeviceControl : UserControl
    {
        DeviceVisualInterface Source;

        public DeviceControl(DeviceVisualInterface source)
        {
            Source = source;
            InitializeComponent();

            source.OnReceiveHarpMessage += (sender, e) => {
                testLabel.Text = e.ToString();
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
            Source.DoCommand("test command");
        }

        private void lineButton0_Click(object sender, EventArgs e)
        {
            Source.DoCommand("line 0");
        }
    }
}
