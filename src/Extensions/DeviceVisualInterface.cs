using Bonsai;
using Bonsai.Harp;
using System;
using System.ComponentModel;
using System.Collections.Generic;
using System.Linq;
using System.Reactive.Linq;

public class DeviceVisualInterface : Combinator<HarpMessage, HarpMessage>
{
    public override IObservable<HarpMessage> Process(IObservable<HarpMessage> source)
    {
        return source.Do(val => {});
    }
}
