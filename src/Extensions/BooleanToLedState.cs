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
public class BooleanToLedState
{
    public IObservable<LedState> Process(IObservable<bool> source)
    {
        return source.Select(value => {
            if (value) {
                return LedState.On;
            } else {
                return LedState.Off;
            }
        });
    }
}
