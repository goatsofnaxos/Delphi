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

[Combinator]
[TypeVisualizer(typeof(DeviceVisualizer))]
public class DeviceVisualInterface
{
    public event EventHandler<HarpMessage> OnReceiveHarpMessage;
    public event EventHandler<string> OnReceiveRuleChange;
    public event EventHandler<string> OnReceiveStateChange;
    public event EventHandler<int> OnReceivePokeCountChange;
    public event EventHandler<int> OnReceiveOdorCountChange;

    public event EventHandler<HarpMessage> Command;

    public void DoCommand(HarpMessage command) {
        Command.Invoke(this, command);
    }

    public IObservable<HarpMessage> Process(IObservable<HarpMessage> source)
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

    public IObservable<HarpMessage> Process(IObservable<HarpMessage> source, IObservable<string> rule, IObservable<string> state, IObservable<int> pokeCount, IObservable<int> odorCount)
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

            // TODO - all these observers should probably be replaced by a single RuleState/poke observer, could have this as a data class in the schema
            var ruleObserver = Observer.Create<string>(
                message =>
                {
                    OnReceiveRuleChange.Invoke(this, message);
                },
                observer.OnError,
                observer.OnCompleted
            );

            var stateObserver = Observer.Create<string>(
                message =>
                {
                    OnReceiveStateChange.Invoke(this, message);
                },
                observer.OnError,
                observer.OnCompleted
            );

            var pokeCountObserver = Observer.Create<int>(
                message =>
                {
                    OnReceivePokeCountChange.Invoke(this, message);
                },
                observer.OnError,
                observer.OnCompleted
            );

            var odorCountObserver = Observer.Create<int>(
                message =>
                {
                    OnReceiveOdorCountChange.Invoke(this, message);
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
                source.SubscribeSafe(sourceObserver),
                rule.SubscribeSafe(ruleObserver),
                state.SubscribeSafe(stateObserver),
                pokeCount.SubscribeSafe(pokeCountObserver),
                odorCount.SubscribeSafe(odorCountObserver)
            );
        });
    }
}
