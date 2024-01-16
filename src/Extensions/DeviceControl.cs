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
        }

        private void DeviceControl_Load(object sender, EventArgs e)
        {

        }
    }
}
