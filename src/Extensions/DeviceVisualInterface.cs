using Bonsai;
using Bonsai.Harp;
using System;
using System.ComponentModel;
using System.Collections.Generic;
using System.Linq;
using System.Reactive.Linq;
using System.Reactive.Disposables;
using System.Reactive;
using Extensions.Extensions;
using System.Runtime.CompilerServices;

[TypeVisualizer(typeof(DeviceVisualizer))]
public class DeviceVisualInterface : Combinator<HarpMessage, HarpMessage>
{
    public event EventHandler<HarpMessage> OnReceiveHarpMessage;

    public event EventHandler<HarpMessage> Command;

    public void DoCommand(HarpMessage command) {
        Command.Invoke(this, command);
    }

    public override IObservable<HarpMessage> Process(IObservable<HarpMessage> source)
    {
        return Observable.Create<HarpMessage>(observer => {
            
            // Source observer is the input handler
            var sourceObserver = Observer.Create<HarpMessage>(
                message => {
                    OnReceiveHarpMessage.Invoke(this, message);
                },
                observer.OnError,
                observer.OnCompleted
            );

            var outputObservable = Observable.FromEventPattern<HarpMessage>(
                handler => Command += handler,
                handler => Command -= handler
            ).Select(evt => evt.EventArgs);

            return new CompositeDisposable(
                outputObservable.Subscribe(observer),
                source.SubscribeSafe(sourceObserver)
            );
        });
    }
}
