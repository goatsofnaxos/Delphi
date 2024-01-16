using Bonsai;
using System;
using System.ComponentModel;
using System.Collections.Generic;
using System.Linq;
using System.Reactive.Linq;
using Bonsai.Harp;

[Combinator]
[Description("")]
[WorkflowElementCategory(ElementCategory.Transform)]
[TypeVisualizer(typeof(DelphiDataVisualizer))]
public class DelphiData
{
    public IObservable<HarpMessage> Process(IObservable<HarpMessage> source)
    {
        return source.Select(value => value);
    }
}

internal class DelphiDataVisualizer
{
}