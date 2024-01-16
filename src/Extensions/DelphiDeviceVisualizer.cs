using Bonsai;
using Bonsai.Design;
using Bonsai.Expressions;
using Bonsai.Harp;
using Extensions.Extensions;
using System;
using System.Windows.Forms;

public class DelphiDeviceVisualizer : DialogTypeVisualizer
{
    DelphiDeviceControl Control;

    public override void Load(IServiceProvider provider)
    {
        var context = (ITypeVisualizerContext)provider.GetService(typeof(ITypeVisualizerContext));
        var visualizerElement = ExpressionBuilder.GetVisualizerElement(context.Source);
        var source = (DelphiDevice)ExpressionBuilder.GetWorkflowElement(visualizerElement.Builder);

        Control = new DelphiDeviceControl(source);
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