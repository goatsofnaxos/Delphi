using Bonsai.Design;
using Bonsai.Expressions;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace Extensions.Extensions
{
    public class DeviceVisualizer : DialogTypeVisualizer
    {
        DeviceControl Control;

        public override void Load(IServiceProvider provider)
        {
            var context = (ITypeVisualizerContext)provider.GetService(typeof(ITypeVisualizerContext));
            var visualizerElement = ExpressionBuilder.GetVisualizerElement(context.Source);
            var source = (DeviceVisualInterface)ExpressionBuilder.GetWorkflowElement(visualizerElement.Builder);

            Control = new DeviceControl(source);
            Control.Dock = DockStyle.Fill;

            var visualizerService = (IDialogTypeVisualizerService)provider.GetService(typeof(IDialogTypeVisualizerService));
            if (visualizerService != null)
            {
                visualizerService.AddControl(Control);
            }
        }

        public override void Show(object value)
        {
        }

        public override void Unload()
        {
            if (Control != null)
            {
                Control.Dispose();
                Control = null;
            }
        }
    }
}
