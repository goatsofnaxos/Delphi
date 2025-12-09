using Bonsai;
using System;
using System.ComponentModel;
using System.Collections.Generic;
using System.Linq;
using System.Reactive.Linq;

[Combinator]
[Description("")]
[WorkflowElementCategory(ElementCategory.Source)]
public class CreateValveConfig
{
    public float HitDuty { get; set; }
    public float HoldDuty { get; set; }
    public uint HitDurationMicroseconds { get; set; }
    public IObservable<byte[]> Process()
    {
        var hitDuty = BitConverter.GetBytes(HitDuty).ToList();
        var holdDuty = BitConverter.GetBytes(HoldDuty).ToList();;
        var hitDuration = BitConverter.GetBytes(HitDurationMicroseconds).ToList();

        var configArray = hitDuty.Concat(holdDuty).Concat(hitDuration).ToArray();

        return Observable.Return(configArray);
    }
}
