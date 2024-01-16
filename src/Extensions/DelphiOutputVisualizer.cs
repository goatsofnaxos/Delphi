using Bonsai;
using Bonsai.Design;
using Bonsai.Harp;
using System;

[assembly: TypeVisualizer(typeof(DelphiOutputVisualizer), Target = typeof(HarpMessage))]

public class DelphiOutputVisualizer : DialogTypeVisualizer
{
    public override void Load(IServiceProvider provider)
    {
        throw new NotImplementedException();
    }

    public override void Show(object value)
    {
        throw new NotImplementedException();
    }

    public override void Unload()
    {
        throw new NotImplementedException();
    }
}